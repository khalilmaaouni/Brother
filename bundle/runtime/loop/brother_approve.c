/* brother-approve: the cut's approval verifier. Only a code from the owner's phone authenticator (RFC 6238) can make
 * it print an approval line, and the seed that makes those codes never leaves this program's own Keychain item.
 *
 *   brother-approve enroll                    once, at a terminal, with Touch ID or the login password: makes the
 *                                             seed, shows the QR
 *   brother-approve verify VERSION SHA CODE   prints: cy1.approval.1 VERSION SHA STEP KEYID HMAC
 *   brother-approve check   < LINE            exit 0 only for a line this item's verify printed; the line is read
 *                                             from standard input (no newline), never argv, which every process sees
 *   brother-approve status                    key id, last accepted step, failures, lockout; no secret
 *
 * There is no read path: no get, show or export. Any other command exits 2. The item lives in the login keychain.
 * TEST BUILD ONLY (-DBROTHER_APPROVE_TEST, scripts/loop/test_brother_approve.py): every command takes one more
 * argument, a legacy file keychain the test created, and the owner presence check reads BROTHER_APPROVE_TEST_PRESENCE
 * (confirmed: present; anything else: the real LocalAuthentication call with every dialog forbidden, so it fails).
 * A production build refuses a keychain argument (exit 2) and has no presence seam: neither is compiled in.
 *
 * OWNER PRESENCE AT ENROLLMENT (CY1.c F1): a terminal proves nothing, since a session can open a pty. Enroll asks
 * LocalAuthentication for the device owner (Touch ID, a paired watch or the login password, in a system dialog no
 * other process can answer) and refuses (exit 8) unless it says yes within two minutes.
 *
 * THE ITEM (service brother-approve, account the user running it) holds the 20 byte seed, the 32 byte HMAC key, the
 * last accepted time step, the consecutive failure count and the lockout end. It is created by THIS program, so the
 * Keychain trusts this program's code identity alone; user interaction is turned off, so an untrusted reader, a
 * locked keychain or a rebuilt binary is a refusal, never a dialog and never an approval.
 *
 * GUESSING (CY1-RESEARCH R1, R1b, R2): every verify takes an exclusive flock on the user's home directory (from the
 * password database, never $HOME, so a caller cannot split the lock), refuses a step at or below the last accepted
 * one, sleeps 2 s inside the lock on any failure, and locks out for 1 hour after 5 consecutive failures.
 *
 * Build: scripts/brother_approve_build.py (fresh salt, codesign -s - -o runtime). An unsalted build is refused: the
 * source ships in every worktree, and an unsalted ad hoc build is reproducible byte for byte (brother_keychain.c, F2).
 */
#include <CommonCrypto/CommonDigest.h>
#include <CommonCrypto/CommonHMAC.h>
#include <Block.h>
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <dispatch/dispatch.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdbool.h>
#include <fcntl.h>
#include <pwd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <time.h>
#include <unistd.h>

#ifndef BROTHER_BUILD_SALT
#error "build with -DBROTHER_BUILD_SALT='\"<random hex>\"' (brother_approve_build.py does); an unsalted build is reproducible"
#endif

#define SERVICE "brother-approve"
#define MAGIC "cy1.approval.1"
#define STATE_MAGIC "cy1s1"
#define STEP_S 30
#define MAX_FAILS 5
#define LOCKOUT_S 3600
#define FAIL_SLEEP_S 2

#define EXIT_ERROR 1      /* keychain locked, item unreadable or corrupt, write or lock failure */
#define EXIT_USAGE 2
#define EXIT_REFUSED 3    /* wrong, replayed or stale code; a line this item did not produce */
#define EXIT_LOCKED 5     /* inside the lockout hour */
#define EXIT_NO_TTY 6
#define EXIT_ENROLLED 7   /* an item already exists */
#define EXIT_NO_PRESENCE 8   /* the owner was not confirmed present at enrollment */
#define LA_POLICY_OWNER 2    /* LAPolicyDeviceOwnerAuthentication: Touch ID, a paired watch or the login password */
#define PRESENCE_WAIT_S 120
#define LINE_MAX_BYTES 299
#define EXIT_ABSENT 44

__attribute__((used)) static const char build_salt[] = BROTHER_BUILD_SALT;   /* part of the signed bytes, never read */

typedef struct {
    unsigned char seed[20];
    unsigned char key[32];
    long long last;       /* last accepted time step, 0 before the first */
    long long fails;      /* consecutive failures */
    long long lock_until; /* unix seconds, 0 when not locked */
} State;

static SecKeychainRef g_kc = NULL;

/* ---------- small helpers ---------- */

static void hexout(const unsigned char *b, size_t n, char *out) {
    static const char *h = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) { out[2 * i] = h[b[i] >> 4]; out[2 * i + 1] = h[b[i] & 15]; }
    out[2 * n] = 0;
}

static int hexin(const char *s, unsigned char *out, size_t n) {
    if (strlen(s) != 2 * n) return -1;
    for (size_t i = 0; i < n; i++) {
        int v = 0;
        for (int j = 0; j < 2; j++) {
            char c = s[2 * i + j];
            int d = (c >= '0' && c <= '9') ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10 : -1;
            if (d < 0) return -1;
            v = v * 16 + d;
        }
        out[i] = (unsigned char)v;
    }
    return 0;
}

static int all_of(const char *s, const char *set, size_t lo, size_t hi) {
    size_t n = strlen(s);
    if (n < lo || n > hi) return 0;
    for (size_t i = 0; i < n; i++) if (!strchr(set, s[i])) return 0;
    return 1;
}

static int valid_version(const char *v) {   /* 1.1.6 style: starts with a digit, then digits, letters, dot, plus, minus */
    return all_of(v, "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.+-", 1, 32) && v[0] >= '0' && v[0] <= '9';
}
static int valid_sha(const char *s) { return all_of(s, "0123456789abcdef", 40, 40); }
static int valid_code(const char *c) { return all_of(c, "0123456789", 6, 6); }

static int parse_ll(const char *s, long long *out) {
    if (!all_of(s, "0123456789", 1, 18)) return -1;
    *out = strtoll(s, NULL, 10);
    return 0;
}

static int same(const char *a, const char *b, size_t n) {   /* constant time over n bytes */
    unsigned char d = 0;
    for (size_t i = 0; i < n; i++) d |= (unsigned char)(a[i] ^ b[i]);
    return d == 0;
}

static void key_id(const State *st, char out[17]) {
    unsigned char h[CC_SHA256_DIGEST_LENGTH];
    char full[2 * CC_SHA256_DIGEST_LENGTH + 1];
    CC_SHA256(st->key, sizeof st->key, h);
    hexout(h, sizeof h, full);
    memcpy(out, full, 16);
    out[16] = 0;
}

/* RFC 6238 (RFC 4226 section 5.3): HMAC-SHA1 over the big endian step, dynamic truncation, 6 digits. */
static void totp(const State *st, long long step, char out[7]) {
    unsigned char msg[8], mac[CC_SHA1_DIGEST_LENGTH];
    for (int i = 7; i >= 0; i--) { msg[i] = (unsigned char)(step & 0xff); step >>= 8; }
    CCHmac(kCCHmacAlgSHA1, st->seed, sizeof st->seed, msg, sizeof msg, mac);
    int off = mac[19] & 0x0f;
    uint32_t bin = ((uint32_t)(mac[off] & 0x7f) << 24) | ((uint32_t)mac[off + 1] << 16) | ((uint32_t)mac[off + 2] << 8) | mac[off + 3];
    snprintf(out, 7, "%06u", bin % 1000000u);
}

static void attest(const State *st, const char *version, const char *sha, long long step, char *line, size_t cap) {
    char kid[17], body[160], hex[2 * CC_SHA256_DIGEST_LENGTH + 1];
    unsigned char mac[CC_SHA256_DIGEST_LENGTH];
    key_id(st, kid);
    snprintf(body, sizeof body, "%s %s %s %lld %s", MAGIC, version, sha, step, kid);
    CCHmac(kCCHmacAlgSHA256, st->key, sizeof st->key, body, strlen(body), mac);
    hexout(mac, sizeof mac, hex);
    snprintf(line, cap, "%s %s", body, hex);
}

/* ---------- the item ---------- */

static CFMutableDictionaryRef item_query(void) {
    struct passwd *pw = getpwuid(getuid());
    if (!pw || !pw->pw_name) return NULL;
    CFStringRef s = CFStringCreateWithCString(NULL, SERVICE, kCFStringEncodingUTF8);
    CFStringRef a = CFStringCreateWithCString(NULL, pw->pw_name, kCFStringEncodingUTF8);
    if (!s || !a) { if (s) CFRelease(s); if (a) CFRelease(a); return NULL; }
    CFMutableDictionaryRef q = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(q, kSecClass, kSecClassGenericPassword);
    CFDictionarySetValue(q, kSecAttrService, s);
    CFDictionarySetValue(q, kSecAttrAccount, a);
    if (g_kc) {
        CFArrayRef list = CFArrayCreate(NULL, (const void **)&g_kc, 1, &kCFTypeArrayCallBacks);
        CFDictionarySetValue(q, kSecMatchSearchList, list);
        CFRelease(list);
    }
    CFRelease(s); CFRelease(a);
    return q;
}

static void encode_state(const State *st, char *out, size_t cap) {
    char seed[41], key[65];
    hexout(st->seed, sizeof st->seed, seed);
    hexout(st->key, sizeof st->key, key);
    snprintf(out, cap, "%s %s %s %lld %lld %lld", STATE_MAGIC, seed, key, st->last, st->fails, st->lock_until);
    memset(seed, 0, sizeof seed); memset(key, 0, sizeof key);
}

/* 0 read, EXIT_ABSENT no item, EXIT_ERROR locked, untrusted or corrupt. Anything unreadable refuses. */
static int read_state(State *st) {
    CFMutableDictionaryRef q = item_query();
    if (!q) return EXIT_ERROR;
    CFDictionarySetValue(q, kSecReturnData, kCFBooleanTrue);
    CFDictionarySetValue(q, kSecMatchLimit, kSecMatchLimitOne);
    CFTypeRef out = NULL;
    OSStatus rc = SecItemCopyMatching(q, &out);
    CFRelease(q);
    if (rc == errSecItemNotFound) return EXIT_ABSENT;
    if (rc != errSecSuccess || !out || CFGetTypeID(out) != CFDataGetTypeID()) {
        if (out) CFRelease(out);
        fprintf(stderr, "brother-approve: the Keychain item cannot be read (%d). Unlock your Keychain.\n", (int)rc);
        return EXIT_ERROR;
    }
    char buf[256] = {0};
    CFIndex n = CFDataGetLength((CFDataRef)out);
    int ok = n > 0 && n < (CFIndex)sizeof buf;
    if (ok) memcpy(buf, CFDataGetBytePtr((CFDataRef)out), (size_t)n);
    CFRelease(out);
    char f[6][130];
    int got = ok ? sscanf(buf, "%129s %129s %129s %129s %129s %129s", f[0], f[1], f[2], f[3], f[4], f[5]) : 0;
    char again[256];
    if (got == 6 && !strcmp(f[0], STATE_MAGIC) && !hexin(f[1], st->seed, sizeof st->seed) && !hexin(f[2], st->key, sizeof st->key)
        && !parse_ll(f[3], &st->last) && !parse_ll(f[4], &st->fails) && !parse_ll(f[5], &st->lock_until)) {
        encode_state(st, again, sizeof again);
        ok = !strcmp(again, buf);   /* exactly the shape this program writes, nothing more */
        memset(again, 0, sizeof again);
    } else {
        ok = 0;
    }
    memset(buf, 0, sizeof buf); memset(f, 0, sizeof f);
    if (!ok) { fprintf(stderr, "brother-approve: the stored state does not parse; refusing.\n"); return EXIT_ERROR; }
    return 0;
}

static int write_state(const State *st, int create) {
    char buf[256];
    encode_state(st, buf, sizeof buf);
    CFDataRef d = CFDataCreate(NULL, (const UInt8 *)buf, (CFIndex)strlen(buf));
    memset(buf, 0, sizeof buf);
    CFMutableDictionaryRef q = item_query();
    if (!q || !d) { if (q) CFRelease(q); if (d) CFRelease(d); return EXIT_ERROR; }
    OSStatus rc;
    if (create) {
        CFDictionarySetValue(q, kSecValueData, d);
        CFStringRef label = CFStringCreateWithCString(NULL, "Brother cut approval", kCFStringEncodingUTF8);
        CFDictionarySetValue(q, kSecAttrLabel, label);
        CFRelease(label);
        if (g_kc) { CFDictionaryRemoveValue(q, kSecMatchSearchList); CFDictionarySetValue(q, kSecUseKeychain, g_kc); }
        rc = SecItemAdd(q, NULL);
    } else {
        CFMutableDictionaryRef upd = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        CFDictionarySetValue(upd, kSecValueData, d);
        rc = SecItemUpdate(q, upd);
        CFRelease(upd);
    }
    CFRelease(q); CFRelease(d);
    if (rc != errSecSuccess) { fprintf(stderr, "brother-approve: the Keychain item cannot be written (%d).\n", (int)rc); return EXIT_ERROR; }
    return 0;
}

static int take_lock(void) {
    struct passwd *pw = getpwuid(getuid());
    if (!pw || !pw->pw_dir) return -1;
    int fd = open(pw->pw_dir, O_RDONLY);
    if (fd < 0 || flock(fd, LOCK_EX) != 0) { fprintf(stderr, "brother-approve: cannot take the lock; refusing.\n"); return -1; }
    return fd;   /* held until exit */
}

/* ---------- QR code, version 6, error correction L, mask 0, byte mode (up to 134 bytes) ---------- */

#define QN 41
static unsigned char qm[QN][QN], qf[QN][QN];   /* module dark?, function module? */

static void qset(int x, int y, int dark) { qm[y][x] = (unsigned char)dark; qf[y][x] = 1; }

static unsigned char gf_mul(unsigned char a, unsigned char b) {
    unsigned r = 0;
    for (int i = 7; i >= 0; i--) { r = (r << 1) ^ ((r >> 7) * 0x11d); r ^= ((b >> i) & 1) * a; }
    return (unsigned char)r;
}

static void rs_ec(const unsigned char *data, int n, unsigned char *ec, int deg) {
    unsigned char gen[32] = {0};
    gen[deg - 1] = 1;
    unsigned char root = 1;
    for (int i = 0; i < deg; i++) {
        for (int j = 0; j < deg; j++) { gen[j] = gf_mul(gen[j], root); if (j + 1 < deg) gen[j] ^= gen[j + 1]; }
        root = gf_mul(root, 0x02);
    }
    memset(ec, 0, (size_t)deg);
    for (int i = 0; i < n; i++) {
        unsigned char f = data[i] ^ ec[0];
        memmove(ec, ec + 1, (size_t)deg - 1);
        ec[deg - 1] = 0;
        for (int j = 0; j < deg; j++) ec[j] ^= gf_mul(gen[j], f);
    }
}

static int qr_encode(const char *text) {
    int len = (int)strlen(text);
    if (len > 134) return -1;
    unsigned char data[136] = {0}, ec[2][18], all[172];
    int bit = 0;
#define PUT(v, nb) for (int _i = (nb) - 1; _i >= 0; _i--, bit++) if (((v) >> _i) & 1) data[bit >> 3] |= (unsigned char)(0x80 >> (bit & 7))
    PUT(4, 4);
    PUT(len, 8);
    for (int i = 0; i < len; i++) PUT((unsigned char)text[i], 8);
    int room = 136 * 8 - bit;
    bit += room < 4 ? room : 4;
    bit = (bit + 7) & ~7;
    for (int pad = 0xec; bit < 136 * 8; pad ^= 0xec ^ 0x11) PUT(pad, 8);
#undef PUT
    rs_ec(data, 68, ec[0], 18);
    rs_ec(data + 68, 68, ec[1], 18);
    int k = 0;
    for (int i = 0; i < 68; i++) { all[k++] = data[i]; all[k++] = data[68 + i]; }
    for (int i = 0; i < 18; i++) { all[k++] = ec[0][i]; all[k++] = ec[1][i]; }
    memset(qm, 0, sizeof qm); memset(qf, 0, sizeof qf);
    for (int i = 0; i < QN; i++) { qset(6, i, i % 2 == 0); qset(i, 6, i % 2 == 0); }
    int fc[3][2] = {{3, 3}, {QN - 4, 3}, {3, QN - 4}};
    for (int c = 0; c < 3; c++)
        for (int dy = -4; dy <= 4; dy++)
            for (int dx = -4; dx <= 4; dx++) {
                int x = fc[c][0] + dx, y = fc[c][1] + dy, d = abs(dx) > abs(dy) ? abs(dx) : abs(dy);
                if (x >= 0 && x < QN && y >= 0 && y < QN) qset(x, y, d != 2 && d != 4);
            }
    for (int dy = -2; dy <= 2; dy++)
        for (int dx = -2; dx <= 2; dx++) qset(34 + dx, 34 + dy, (abs(dx) > abs(dy) ? abs(dx) : abs(dy)) != 1);
    int fmt = 1 << 3, rem = fmt;   /* level L is 01, mask 000 */
    for (int i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537);
    int bits = ((fmt << 10) | rem) ^ 0x5412;
#define B(i) ((bits >> (i)) & 1)
    for (int i = 0; i <= 5; i++) qset(8, i, B(i));
    qset(8, 7, B(6)); qset(8, 8, B(7)); qset(7, 8, B(8));
    for (int i = 9; i < 15; i++) qset(14 - i, 8, B(i));
    for (int i = 0; i < 8; i++) qset(QN - 1 - i, 8, B(i));
    for (int i = 8; i < 15; i++) qset(8, QN - 15 + i, B(i));
    qset(8, QN - 8, 1);
#undef B
    int i = 0;
    for (int right = QN - 1; right >= 1; right -= 2) {
        if (right == 6) right = 5;
        for (int vert = 0; vert < QN; vert++)
            for (int j = 0; j < 2; j++) {
                int x = right - j, up = ((right + 1) & 2) == 0, y = up ? QN - 1 - vert : vert;
                if (!qf[y][x] && i < 172 * 8) { qm[y][x] = (all[i >> 3] >> (7 - (i & 7))) & 1; i++; }
            }
    }
    for (int y = 0; y < QN; y++)
        for (int x = 0; x < QN; x++)
            if (!qf[y][x] && (x + y) % 2 == 0) qm[y][x] ^= 1;
    memset(data, 0, sizeof data); memset(all, 0, sizeof all); memset(ec, 0, sizeof ec);
    return 0;
}

/* Two modules per character (upper half block), black on white, 4 module quiet zone: 49 columns by 25 rows. */
static void qr_print(FILE *t) {
    for (int y = -4; y < QN + 4; y += 2) {
        for (int x = -4; x < QN + 4; x++) {
            int top = (x >= 0 && x < QN && y >= 0 && y < QN) ? qm[y][x] : 0;
            int bot = (x >= 0 && x < QN && y + 1 >= 0 && y + 1 < QN) ? qm[y + 1][x] : 0;
            fprintf(t, "\033[%d;%dm\xe2\x96\x80", top ? 30 : 97, bot ? 40 : 107);
        }
        fprintf(t, "\033[0m\n");
    }
}

/* ---------- owner presence (LocalAuthentication through the Objective C runtime, so this file stays C) ---------- */

/* 1 only when LocalAuthentication answers yes for the device owner. interactive 0 forbids every dialog, so the call
 * fails with LAErrorNotInteractive: the test build drives this exact code that way without a prompt. */
static int la_presence(int interactive) {
    id cls = (id)objc_getClass("LAContext");
    if (!cls) return 0;
    id ctx = ((id (*)(id, SEL))objc_msgSend)(cls, sel_registerName("new"));
    if (!ctx) return 0;
    if (!interactive) ((void (*)(id, SEL, bool))objc_msgSend)(ctx, sel_registerName("setInteractionNotAllowed:"), true);
    __block int ok = 0;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    void (^reply)(bool, id) = Block_copy(^(bool success, id error) {
        (void)error;
        ok = success ? 1 : 0;
        dispatch_semaphore_signal(done);
    });
    ((void (*)(id, SEL, long, CFStringRef, void (^)(bool, id)))objc_msgSend)(
        ctx, sel_registerName("evaluatePolicy:localizedReason:reply:"), (long)LA_POLICY_OWNER,
        CFSTR("enroll the Brother cut approval key"), reply);
    long late = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, (int64_t)PRESENCE_WAIT_S * NSEC_PER_SEC));
    if (late) ((void (*)(id, SEL))objc_msgSend)(ctx, sel_registerName("invalidate"));
    return late ? 0 : ok;   /* no answer in time is no owner; the context and block live until exit */
}

static int owner_present(void) {
#ifdef BROTHER_APPROVE_TEST
    const char *seam = getenv("BROTHER_APPROVE_TEST_PRESENCE");
    if (seam && !strcmp(seam, "confirmed")) return 1;
    return la_presence(0);
#else
    return la_presence(1);
#endif
}

/* ---------- commands ---------- */

static int do_enroll(void) {
    if (!isatty(0) || !isatty(1)) { fprintf(stderr, "brother-approve: enroll runs only in Terminal, by you.\n"); return EXIT_NO_TTY; }
    FILE *t = fopen("/dev/tty", "w");
    if (!t) { fprintf(stderr, "brother-approve: cannot open the terminal.\n"); return EXIT_NO_TTY; }
    if (take_lock() < 0) { fclose(t); return EXIT_ERROR; }
    State st;
    memset(&st, 0, sizeof st);
    int rc = read_state(&st);
    memset(&st, 0, sizeof st);
    if (rc != EXIT_ABSENT) {   /* an item, readable or not, is never replaced: the key id other files pin stays */
        fprintf(stderr, "brother-approve: already enrolled; refusing a second item.\n");
        fclose(t);
        return EXIT_ENROLLED;
    }
    fprintf(t, "Confirm with Touch ID or your login password in the dialog that opens.\n");
    fflush(t);
    if (!owner_present()) {
        fprintf(stderr, "brother-approve: the owner was not confirmed (Touch ID or the login password); nothing enrolled.\n");
        fclose(t);
        return EXIT_NO_PRESENCE;
    }
    if (SecRandomCopyBytes(kSecRandomDefault, sizeof st.seed, st.seed) != errSecSuccess
        || SecRandomCopyBytes(kSecRandomDefault, sizeof st.key, st.key) != errSecSuccess) {
        fclose(t); return EXIT_ERROR;
    }
    State back;
    memset(&back, 0, sizeof back);   /* memcmp below covers the padding too */
    if (write_state(&st, 1) != 0 || read_state(&back) != 0 || memcmp(&back, &st, sizeof st)) {
        fprintf(stderr, "brother-approve: the item was not confirmed written.\n");
        memset(&st, 0, sizeof st); memset(&back, 0, sizeof back); fclose(t);
        return EXIT_ERROR;
    }
    memset(&back, 0, sizeof back);
    static const char *b32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    char secret[33], uri[200], kid[17];
    for (int i = 0; i < 32; i++) {   /* 20 bytes are exactly 160 bits, 32 base32 characters, no padding */
        int pos = i * 5, v = 0;
        for (int b = 0; b < 5; b++) v = (v << 1) | ((st.seed[(pos + b) >> 3] >> (7 - ((pos + b) & 7))) & 1);
        secret[i] = b32[v];
    }
    secret[32] = 0;
    snprintf(uri, sizeof uri, "otpauth://totp/Brother:cut?secret=%s&issuer=Brother&algorithm=SHA1&digits=6&period=30", secret);
    key_id(&st, kid);
    rc = qr_encode(uri);
    fprintf(t, "Scan this with your phone authenticator. It is shown once.\n\n");
    if (rc == 0) qr_print(t);
    fprintf(t, "\nOr add it by hand: %s\nKey id (put it in docs/plan/APPROVER-KEY.json): %s\n", uri, kid);
    fflush(t);
    fclose(t);
    memset(secret, 0, sizeof secret); memset(uri, 0, sizeof uri); memset(&st, 0, sizeof st); memset(qm, 0, sizeof qm);
    return rc == 0 ? 0 : EXIT_ERROR;
}

static int do_verify(const char *version, const char *sha, const char *code) {
    if (!valid_version(version) || !valid_sha(sha) || !valid_code(code)) {
        fprintf(stderr, "brother-approve: verify VERSION SHA CODE (a version, the full 40 hex sha, six digits)\n");
        return EXIT_USAGE;
    }
    if (take_lock() < 0) return EXIT_ERROR;
    State st;
    int rc = read_state(&st);
    if (rc != 0) { if (rc == EXIT_ABSENT) fprintf(stderr, "brother-approve: not enrolled.\n"); return rc; }
    time_t now = time(NULL);
    if (st.lock_until > (long long)now) {
        memset(&st, 0, sizeof st);
        fprintf(stderr, "brother-approve: locked out after %d wrong codes; try again later.\n", MAX_FAILS);
        sleep(FAIL_SLEEP_S);
        return EXIT_LOCKED;
    }
    long long step = (long long)now / STEP_S, accepted = -1;
    for (long long s = step; s >= step - 1; s--) {   /* RFC 6238 5.2: the current step and at most one back */
        char want[7];
        totp(&st, s, want);
        if (s > st.last && same(want, code, 6)) { accepted = s; break; }
    }
    if (accepted < 0) {
        st.fails += 1;
        if (st.fails >= MAX_FAILS) { st.lock_until = (long long)now + LOCKOUT_S; st.fails = 0; }
        rc = write_state(&st, 0);
        memset(&st, 0, sizeof st);
        fprintf(stderr, "brother-approve: code refused.\n");
        sleep(FAIL_SLEEP_S);
        return rc != 0 ? EXIT_ERROR : EXIT_REFUSED;
    }
    st.last = accepted;
    st.fails = 0;
    st.lock_until = 0;
    if (write_state(&st, 0) != 0) { memset(&st, 0, sizeof st); return EXIT_ERROR; }   /* not recorded: no line */
    char line[300];
    attest(&st, version, sha, accepted, line, sizeof line);
    memset(&st, 0, sizeof st);
    printf("%s\n", line);
    return 0;
}

static int do_check(void) {
    char line[LINE_MAX_BYTES + 2], f[7][80];
    size_t n = fread(line, 1, sizeof line - 1, stdin);   /* the whole of stdin is the line: no newline, no NUL */
    if (ferror(stdin) || n == 0 || n > LINE_MAX_BYTES) { fprintf(stderr, "brother-approve: not an approval line.\n"); return EXIT_REFUSED; }
    line[n] = 0;
    for (size_t i = 0; i < n; i++)
        if ((unsigned char)line[i] < 0x21 && line[i] != ' ') { fprintf(stderr, "brother-approve: not an approval line.\n"); return EXIT_REFUSED; }
    int got = sscanf(line, "%79s %79s %79s %79s %79s %79s %79s", f[0], f[1], f[2], f[3], f[4], f[5], f[6]);
    long long step;
    if (got != 6 || strcmp(f[0], MAGIC) || !valid_version(f[1]) || !valid_sha(f[2]) || parse_ll(f[3], &step)
        || !all_of(f[4], "0123456789abcdef", 16, 16) || !all_of(f[5], "0123456789abcdef", 64, 64)) {
        fprintf(stderr, "brother-approve: not an approval line.\n");
        return EXIT_REFUSED;
    }
    State st;
    int rc = read_state(&st);
    if (rc != 0) { if (rc == EXIT_ABSENT) fprintf(stderr, "brother-approve: not enrolled.\n"); return rc; }
    char want[300];
    attest(&st, f[1], f[2], step, want, sizeof want);
    memset(&st, 0, sizeof st);
    if (strlen(want) != n || !same(want, line, n)) { fprintf(stderr, "brother-approve: approval refused.\n"); return EXIT_REFUSED; }
    printf("ok %s %s %lld %s\n", f[1], f[2], step, f[4]);
    return 0;
}

static int do_status(void) {
    State st;
    int rc = read_state(&st);
    if (rc != 0) { if (rc == EXIT_ABSENT) fprintf(stderr, "brother-approve: not enrolled.\n"); return rc; }
    char kid[17];
    key_id(&st, kid);
    printf("enrolled key_id=%s last_step=%lld failures=%lld locked_until=%lld\n", kid, st.last, st.fails, st.lock_until);
    memset(&st, 0, sizeof st);
    return 0;
}

static int usage(void) {
    fprintf(stderr, "usage: brother-approve enroll|verify VERSION SHA CODE|check (the line on stdin)|status\n");
    return EXIT_USAGE;
}

int main(int argc, char **argv) {
    if (argc < 2) return usage();
    const char *cmd = argv[1];
    int want = !strcmp(cmd, "enroll") || !strcmp(cmd, "status") || !strcmp(cmd, "check") ? 0 : !strcmp(cmd, "verify") ? 3 : -1;
#ifdef BROTHER_APPROVE_TEST
    if (want < 0 || argc != 3 + want) return usage();   /* the test build always names its own keychain */
#else
    if (want < 0 || argc != 2 + want) return usage();   /* no keychain argument: the login keychain only */
#endif
    for (int i = 2; i < argc; i++) if (!argv[i][0]) return usage();
    SecKeychainSetUserInteractionAllowed(false);   /* a locked keychain or an untrusted reader refuses, never prompts */
#ifdef BROTHER_APPROVE_TEST
    if (SecKeychainOpen(argv[argc - 1], &g_kc) != errSecSuccess || !g_kc) {
        fprintf(stderr, "brother-approve: cannot open keychain\n");
        return EXIT_ERROR;
    }
#endif
    int rc;
    if (!strcmp(cmd, "enroll")) rc = do_enroll();
    else if (!strcmp(cmd, "verify")) rc = do_verify(argv[2], argv[3], argv[4]);
    else if (!strcmp(cmd, "check")) rc = do_check();
    else rc = do_status();
    if (g_kc) CFRelease(g_kc);
    return rc;
}
