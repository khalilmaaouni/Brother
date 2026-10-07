/* brother-keychain: the loop's own Keychain reader, built and ad-hoc signed by brother_login.py into
 * ~/.claude/bin, a folder no native seat can read or run from. The item it creates is trusted to THIS program's
 * code identity alone, so /usr/bin/security (which any seat on the off lane can still run, see D10) does not read
 * it silently: the Keychain asks the person instead.
 *
 *   brother-keychain get    SERVICE ACCOUNT [KEYCHAIN]   prints the value and a newline; exit 44 when absent
 *   brother-keychain set    SERVICE ACCOUNT [KEYCHAIN]   reads the value from stdin (never argv); replaces an item
 *                                                        this program created, exit 45 when a foreign item is there
 *   brother-keychain delete SERVICE ACCOUNT [KEYCHAIN]   exit 0 when gone (44 when it was already absent)
 *
 * KEYCHAIN names a legacy file keychain (the login keychain by default); tests hand in one they created.
 * Exit 2 on a usage error, a SERVICE or ACCOUNT that is not UTF-8 included (review 2026-10-03, F4: it aborted).
 * Build: clang -DBROTHER_BUILD_SALT='"<random hex>"' -framework Security -framework CoreFoundation -o brother-keychain
 *        brother_keychain.c && codesign -s - brother-keychain
 *
 * THE SALT (review 2026-10-03, F2): an ad-hoc signature's identity is the hash of the program's bytes, and this source
 * ships inside every seat's worktree, so an unsalted build is reproducible: a seat could compile the same bytes, get
 * the same identity, and the Keychain would trust its copy. brother_login.py builds with a fresh random salt, so the
 * trusted identity exists once, in ~/.claude/bin, where no seat can read or run it. A build without one is refused.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef BROTHER_BUILD_SALT
#error "build with -DBROTHER_BUILD_SALT='\"<random hex>\"' (brother_login.py does); an unsalted build is reproducible"
#endif

#define EXIT_ABSENT 44
#define EXIT_FOREIGN 45
#define EXIT_USAGE 2

__attribute__((used)) static const char build_salt[] = BROTHER_BUILD_SALT;   /* part of the signed bytes, never read */

static CFStringRef cfs(const char *s) { return CFStringCreateWithCString(NULL, s, kCFStringEncodingUTF8); }

static SecKeychainRef open_keychain(const char *path) {
    if (!path) return NULL;
    SecKeychainRef kc = NULL;
    if (SecKeychainOpen(path, &kc) != errSecSuccess) return NULL;
    return kc;
}

static CFMutableDictionaryRef query(const char *service, const char *account, SecKeychainRef kc) {
    CFMutableDictionaryRef q = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFStringRef s = cfs(service), a = cfs(account);
    if (!s || !a) {   /* not UTF-8: CFStringCreateWithCString returns NULL, and a NULL value aborts the program */
        if (s) CFRelease(s);
        if (a) CFRelease(a);
        CFRelease(q);
        return NULL;
    }
    CFDictionarySetValue(q, kSecClass, kSecClassGenericPassword);
    CFDictionarySetValue(q, kSecAttrService, s);
    CFDictionarySetValue(q, kSecAttrAccount, a);
    if (kc) {
        CFArrayRef list = CFArrayCreate(NULL, (const void **)&kc, 1, &kCFTypeArrayCallBacks);
        CFDictionarySetValue(q, kSecMatchSearchList, list);
        CFRelease(list);
    }
    CFRelease(s); CFRelease(a);
    return q;
}

static int do_get(const char *service, const char *account, SecKeychainRef kc) {
    CFMutableDictionaryRef q = query(service, account, kc);
    if (!q) return EXIT_USAGE;
    CFDictionarySetValue(q, kSecReturnData, kCFBooleanTrue);
    CFDictionarySetValue(q, kSecMatchLimit, kSecMatchLimitOne);
    CFTypeRef out = NULL;
    OSStatus st = SecItemCopyMatching(q, &out);
    CFRelease(q);
    if (st == errSecItemNotFound) return EXIT_ABSENT;
    if (st != errSecSuccess || !out) { fprintf(stderr, "brother-keychain: read failed (%d)\n", (int)st); return 1; }
    CFDataRef d = (CFDataRef)out;
    fwrite(CFDataGetBytePtr(d), 1, (size_t)CFDataGetLength(d), stdout);
    fputc('\n', stdout);
    CFRelease(out);
    return 0;
}

static int do_delete(const char *service, const char *account, SecKeychainRef kc) {
    CFMutableDictionaryRef q = query(service, account, kc);
    if (!q) return EXIT_USAGE;
    OSStatus st = SecItemDelete(q);
    CFRelease(q);
    if (st == errSecItemNotFound) return EXIT_ABSENT;
    if (st != errSecSuccess) { fprintf(stderr, "brother-keychain: delete failed (%d)\n", (int)st); return 1; }
    return 0;
}

static int do_set(const char *service, const char *account, SecKeychainRef kc) {
    char buf[8192];
    size_t n = fread(buf, 1, sizeof buf, stdin);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) n--;
    if (n == 0 || n >= sizeof buf - 1) { fprintf(stderr, "brother-keychain: no value on stdin\n"); return 2; }
    /* Replace only an item this program can open; a foreign item (created by another program) stays untouched. */
    int had = do_delete(service, account, kc);
    if (had == EXIT_USAGE) { memset(buf, 0, sizeof buf); return EXIT_USAGE; }
    if (had != 0 && had != EXIT_ABSENT) return EXIT_FOREIGN;
    CFMutableDictionaryRef q = query(service, account, kc);
    if (!q) { memset(buf, 0, sizeof buf); return EXIT_USAGE; }
    CFDataRef d = CFDataCreate(NULL, (const UInt8 *)buf, (CFIndex)n);
    CFDictionarySetValue(q, kSecValueData, d);
    if (kc) CFDictionarySetValue(q, kSecUseKeychain, kc);
    OSStatus st = SecItemAdd(q, NULL);
    CFRelease(d); CFRelease(q);
    memset(buf, 0, sizeof buf);
    if (st != errSecSuccess) { fprintf(stderr, "brother-keychain: write failed (%d)\n", (int)st); return 1; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 4 || argc > 5) { fprintf(stderr, "usage: brother-keychain get|set|delete SERVICE ACCOUNT [KEYCHAIN]\n"); return 2; }
    SecKeychainRef kc = argc == 5 ? open_keychain(argv[4]) : NULL;
    if (argc == 5 && !kc) { fprintf(stderr, "brother-keychain: cannot open keychain\n"); return 1; }
    int rc;
    if (!strcmp(argv[1], "get")) rc = do_get(argv[2], argv[3], kc);
    else if (!strcmp(argv[1], "set")) rc = do_set(argv[2], argv[3], kc);
    else if (!strcmp(argv[1], "delete")) rc = do_delete(argv[2], argv[3], kc);
    else { fprintf(stderr, "usage: brother-keychain get|set|delete SERVICE ACCOUNT [KEYCHAIN]\n"); rc = 2; }
    if (kc) CFRelease(kc);
    return rc;
}
