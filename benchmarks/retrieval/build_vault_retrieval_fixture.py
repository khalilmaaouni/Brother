#!/usr/bin/env python3
"""Build the synthetic Brother Vault retrieval fixture and its two query corpora.

WHY A GENERATOR RATHER THAN 86 HAND WRITTEN NOTES: the corpus has to declare,
per query, how many notes actually carry that anchor (`anchor_note_count`), and
that number is what decides the band. Counting it by hand across 86 notes is
exactly the arithmetic a person gets wrong, so this file PLACES the anchors and
then MEASURES the result with bm_vault.py's own ANCHOR regex, writing the
measured count, never the intended one. Re-running it is deterministic: no clock,
no randomness beyond one fixed seed, so a rebuild produces byte identical output.

WHAT THE FIXTURE IS FOR: scripts/score_vault_retrieval.py runs the REAL
`bm_vault.py check --paths <basename> --limit N` call the point of need hook
makes (products/brothermode/tools/vault_recall_hook.py:1342) against this vault,
in an isolated index, and scores where the true note landed. Every failure shape
RR3 measured against the founder's real vault is reproduced here with invented
content, so the benchmark ships publicly:

  (a) rare anchors, one note per file name
  (b) crowded anchors, 9 or more notes sharing one file name, one true lesson
  (c) near duplicate notes, same normalized stem in two folders, identical body
  (d) three projects (alpha-app, beta-cli, gamma-site) where one basename is
      carried by notes in two different projects
  (e) four notes whose name and description are Japanese
  (f) five source_of_record decoys that crowd many queries and are never the
      expected note, so an authority first sort is punished by the score
  (g) four notes carrying no anchor at all
  (h) WHERE the file lives. Every anchored note names its own file once as a
      repo-relative path (<project>/<module>/<name>), and the query's file_path
      puts the edited file at that same path, so the nine notes crowding one
      basename can be told apart by the directory the caller is working in and
      the crowded band is winnable rather than a coin toss. The decoys name a
      directory of their own (<project>/standards/...), so a ranker cannot win
      by preferring any note that merely carries a path shaped anchor.

Every service name, file name, lesson and Japanese sentence here is invented for
this fixture. No text from any real vault, no client terms, no person names.

Python 3.9, standard library only. No em or en dashes anywhere in this file.

Usage:  python3 benchmarks/retrieval/build_vault_retrieval_fixture.py [--out DIR]
"""
import argparse
import importlib.util
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_OUT = os.path.join(REPO, "benchmarks", "fixtures", "vault-retrieval")
TOOL = os.path.join(REPO, "products", "brothermode", "tools", "bm_vault.py")

SEED = 20260908
N_PRIMARY = 60          # the base block: one primary note per query, 40 tuning plus 20 held out
N_DECOY = 5             # shape (f)
N_UNANCHORED = 4        # shape (g)
DUPLICATED = (3, 17)    # shape (c): these two primaries are copied into a second folder

BAND_PLAN = ["rare"] * 23 + ["mid"] * 22 + ["crowded"] * 15
TUNING_QUOTA = {"rare": 15, "mid": 15, "crowded": 10}

PROJECTS = ("alpha-app", "beta-cli", "gamma-site")

# THE HELD OUT EXTENSION (VR0b). score_vault_retrieval.py prints NO-DATA below 30
# queries, and the base split leaves the held out set at 20, so a held out score
# could never be read. Ten more primaries are appended AFTER the base block rather
# than folded into it, and this is the whole reason the extension is a separate
# block: the tuning corpus must not move. Every random draw the base block makes
# happens before the extension exists, the extension mints anchors 60 to 69 out of
# the same already shuffled pair list, and no extension note ever carries a base
# anchor, so all 40 tuning query records rebuild byte identical. The one field that
# does move is vault_fingerprint, which counts the vault both corpora share.
EXT_N = 10
EXT_DECOY = 5           # shape (f) again, minted fresh so no base decoy note changes
# rare 4, mid 3, crowded 3, which puts the held out mix at 12 / 10 / 8 against the
# 8 / 7 / 5 it had (0.400 / 0.333 / 0.267 against 0.400 / 0.350 / 0.250). Thirty
# does not divide the old proportions exactly; this is the nearest whole split.
EXT_BANDS = ["rare", "mid", "crowded", "rare", "mid",
             "crowded", "rare", "mid", "crowded", "rare"]
# Folder, project and note type per extension primary, spread the way the base
# block spreads: mostly failures, then one lesson folder per project, then reference.
EXT_FOLDERS = [
    ("40-Failures", "alpha-app", "failure"),
    ("40-Failures", "beta-cli", "failure"),
    ("40-Failures", "gamma-site", "failure"),
    ("40-Failures", "alpha-app", "failure"),
    ("10-Projects/alpha-app", "alpha-app", "lesson"),
    ("10-Projects/alpha-app", "alpha-app", "lesson"),
    ("10-Projects/beta-cli", "beta-cli", "lesson"),
    ("10-Projects/beta-cli", "beta-cli", "lesson"),
    ("10-Projects/gamma-site", "gamma-site", "lesson"),
    ("50-Reference", "beta-cli", "reference"),
]
TOTAL_PRIMARY = N_PRIMARY + EXT_N


def _folder(i):
    """Folder, project and type per primary index. A 40-Failures note still declares
    a project, the same way a real failure note does."""
    if i < 26:
        return "40-Failures", PROJECTS[i % 3], "failure"
    if i < 34:
        return "10-Projects/alpha-app", "alpha-app", "lesson"
    if i < 42:
        return "10-Projects/beta-cli", "beta-cli", "lesson"
    if i < 50:
        return "10-Projects/gamma-site", "gamma-site", "lesson"
    if i < N_PRIMARY:
        return "50-Reference", PROJECTS[i % 3], "reference"
    return EXT_FOLDERS[i - N_PRIMARY]


SUBJECTS = [
    "sync worker", "batch loader", "request router", "page cache", "nightly digest",
    "ledger writer", "roster importer", "health beacon", "parcel tracker", "quota meter",
    "tally job", "queue warden", "retry ripple", "build anvil", "tile mosaic",
    "orbit scheduler", "log plume", "index quartz", "colour picker", "tundra sweeper",
    "seat allocator", "invoice folder", "token minter", "shard picker",
]
PREDICATES = [
    "counted a skipped row as a written one",
    "reported a partial run as a complete one",
    "read a stale copy for a whole afternoon",
    "answered from a cache nobody invalidated",
    "kept a lock past the transaction that took it",
    "wrote its output to a path it never created",
    "retried forever on a permanent failure",
    "logged the wrong identifier on every line",
    "rounded a total before it was summed",
    "trusted a header the client controls",
    "compared two timestamps in different zones",
    "dropped the last page of every listing",
    "treated an empty result as an error",
]
FIXES = [
    "count what was written, never what was offered",
    "print the number of rows the writer actually accepted",
    "invalidate on write, not on a timer nobody watches",
    "let the failing call name the record it failed on",
    "release the lock in the same block that took it",
    "create the directory before the first write, once",
    "give the retry a ceiling and a reason to stop",
    "carry the identifier through as one field, not two",
    "sum first, round last, and say which one you printed",
    "derive trust from the session, never from a header",
    "store one zone and convert at the edge",
    "page until the cursor is empty, not until the page is short",
    "an empty result is an answer, so return it as one",
]

WORDS_A = [
    "alpha", "beta", "gamma", "delta", "kappa", "lambda", "sigma", "tau",
    "amber", "basalt", "cedar", "dune", "ember", "fjord", "granite", "harbor",
    "indigo", "juniper", "kelp", "larch", "marble", "nimbus", "onyx", "pumice",
]
WORDS_B = [
    "sync", "loader", "router", "cache", "digest", "ledger", "roster", "beacon",
    "parcel", "quota", "tally", "warden", "ripple", "anvil", "mosaic", "orbit",
    "plume", "quartz", "picker", "tundra", "seat", "invoice", "token", "shard",
]
EXT_FORMS = [
    "{a}_{b}.py",
    "{a}-{b}.ts",
    "{a}_{b}.sh",
    "{a}_{b}.yml",
    "{a}_{b}.json",
    "{A}{B}.swift",
    "{A}-{b}-NOTES.md",
]

# Five Japanese notes (shape e), spread so some land in each split. The fifth sits
# in the extension block, which keeps the held out Japanese share where it was.
JA_INDICES = (5, 12, 30, 47, 65)
JA_NAMES = [
    "同じ名前のファイルが二つの案件にあると取り違える",
    "夜間バッチの失敗が翌朝まで気づかれない",
    "キャッシュを消す担当が決まっていない",
    "設定の既定値が本番と検証で違う",
    "検証用の設定が本番の設定を上書きしていた",
]
JA_DESCRIPTIONS = [
    "二つの案件に同じ基底名のファイルがあり、{anchor} の記録がもう一方の案件の作業中に出てきた。",
    "{anchor} の夜間実行が失敗しても通知が出ず、翌朝まで誰も気づかなかった。",
    "{anchor} のキャッシュを誰が消すのか決まっておらず、古い値が一日残った。",
    "{anchor} の既定値が本番と検証で異なり、検証で通った設定が本番で落ちた。",
    "{anchor} の検証用の設定が本番の設定を上書きし、切り戻すまで誰も気づかなかった。",
]
JA_BODIES = [
    "基底名だけで記録を引くと案件をまたいで一致してしまう。{anchor} を扱うときは案件名も一緒に確認する。",
    "{anchor} の失敗を通知に載せる。出力が空でも成功とは限らない。",
    "{anchor} の無効化を書き込み側の処理に含める。時間任せにしない。",
    "{anchor} の既定値を一箇所に置き、両方の環境が同じ値を読む。",
    "{anchor} は環境ごとに別の設定を読む。上書きの順序を記録に残し、どちらが先に勝つかを書いておく。",
]
JA_FIX = "案件名と基底名を必ず一緒に記録する"
#: Shape (h) in Japanese, so a Japanese note does not carry one English line.
JA_LOCATION_LINE = "この記録が指すファイルは %s にある。"

DECOY_NAMES = [
    "the release checklist every team is expected to follow",
    "the naming rules for jobs, queues and their owners",
    "the retention ruling for logs and generated files",
    "the approved list of runtimes and their support windows",
    "the definition of a completed change and who signs it",
]
DECOY_DESCRIPTIONS = [
    "The approved checklist a change passes before it is called done.",
    "The approved naming rules for background jobs and the queues they read.",
    "The approved retention window for logs, caches and generated output.",
    "The approved runtimes, with the date each one stops being supported.",
    "The approved definition of done, and the role that signs each step.",
]

EXT_DECOY_NAMES = [
    "the escalation path for an incident found outside working hours",
    "the rules for what a scheduled job may write and where",
    "the review record every change carries before it is merged",
    "the supported storage tiers and what each one guarantees",
    "the wording every status page uses for a partial outage",
]
EXT_DECOY_DESCRIPTIONS = [
    "The approved escalation path, with the role that answers at each step.",
    "The approved write locations for a scheduled job, and what is forbidden.",
    "The approved review record, and the fields a merge is refused without.",
    "The approved storage tiers, with the guarantee each one carries.",
    "The approved wording for a partial outage, published exactly as written here.",
]

UNANCHORED_NAMES = [
    "a review that read the summary and not the change",
    "an estimate given before anyone opened the ticket",
    "a decision recorded without the option it beat",
    "a status that reported effort instead of outcome",
]
UNANCHORED_BODIES = [
    "The review approved a summary of the change rather than the change, so a "
    "rewritten paragraph and a rewritten rule read the same on the page.",
    "The estimate was given in a corridor, before anyone had opened the ticket, "
    "and it became the deadline everyone planned against.",
    "The decision was written down with its outcome and none of its alternatives, "
    "so nobody later could tell what it had been chosen over.",
    "The status said how many hours had been spent, which is effort, and never said "
    "what a reader could now do that they could not do the week before.",
]


def _load_anchor_regex():
    """bm_vault.py's own ANCHOR regex, imported by path. Never re-implemented here:
    the whole oracle rests on the fixture's anchors being the ones the indexer
    actually extracts, so a second copy of the pattern would be a second opinion."""
    spec = importlib.util.spec_from_file_location("bm_vault_for_anchors", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ANCHOR


def _anchor_names(rng, count):
    """`count` distinct file shaped anchors, one per primary note. The shuffle is the
    only draw this makes, so asking for more anchors hands back the same leading run:
    that is what lets the held out extension mint anchors 60 upward without moving a
    single base anchor, and so without moving the tuning corpus."""
    seen, out = set(), []
    pairs = [(a, b) for a in WORDS_A for b in WORDS_B]
    rng.shuffle(pairs)
    i = 0
    for a, b in pairs:
        form = EXT_FORMS[i % len(EXT_FORMS)]
        name = form.format(a=a, b=b, A=a.capitalize(), B=b.capitalize())
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        i += 1
        if len(out) == count:
            return out
    raise SystemExit("NO-DATA: could not mint %d distinct anchors" % count)


def _target_count(band, j):
    if band == "rare":
        return 1
    if band == "mid":
        return 3 + (j % 5)          # 3 to 7, one clear step below the crowded floor
    return 9 + (j % 4)              # 9 to 12


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _module_dir(anchor):
    """The directory this fixture says the anchored file lives in, one per anchor.

    Shape (h). A note that records a lesson about a file knows where that file is,
    and the directory is the only thing a ranker can use to tell nine notes about
    one basename apart. Derived from the anchor's own stem, so it is deterministic
    and (the anchors being minted from distinct word pairs) unique per anchor. The
    uniqueness is ASSERTED in build(), never argued: two notes sharing a module
    directory would both match the same context and the crowded band would be a
    coin toss again."""
    return _slug(anchor.rsplit(".", 1)[0])


def _home_dir(i, anchor):
    """<project>/<module>: the two directory segments the point of need hook's
    context actually carries.

    vault_recall_hook._context_path falls back to the LAST THREE segments of a path
    that is in no repository, which is exactly project, module and file name, so a
    query file_path of /repo/<project>/<module>/<anchor> reaches bm_vault.py as
    <project>/<module>/<anchor> and its two directory segments are the ones this
    note names. A deeper home would have its leading segments dropped by that
    fallback and the note would name a directory the caller never sends."""
    return "%s/%s" % (_folder(i)[1], _module_dir(anchor))


def _decoy_home(project, name):
    """Where a source_of_record page keeps itself. A directory of its own, never a
    project source directory, so a decoy carries a path shaped anchor that shares no
    module segment with any query's context."""
    return "%s/standards/%s.md" % (project, _slug(name)[:60])


def _name_of(text):
    m = re.search(r"^name:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _why(band, holders, primary, project):
    twin = [h for h in holders
            if h != primary and os.path.basename(h) == os.path.basename(primary)]
    if twin:
        return ("shape (c): this note is filed twice under one stem, here and at %s, with an "
                "identical body. bm_vault.py serves one title once, so whichever copy ranks "
                "first suppresses the other and the losing copy is a miss on primary_path "
                "even though first_expected_rank resolves" % twin[0])
    if band == "rare":
        return "one note carries this anchor; exact anchor match alone should answer it"
    projects = set()
    for h in holders:
        parts = h.split("/")
        projects.add(parts[1] if h.startswith("10-Projects/") else parts[0])
    extra = ""
    if len(projects) > 1:
        extra = "; carriers span %s, so nothing prefers %s's own tree" % (
            ", ".join(sorted(projects)), project)
    return ("%d notes carry this anchor and only %s is the lesson for it%s"
            % (len(holders), os.path.basename(primary), extra))


def _render(note_id, name, ntype, authority, project, tags, descr, lesson, fix, others,
            location=None, location_line=None):
    lines = [
        "---",
        "id: %s" % note_id,
        "name: %s" % name,
        "type: %s" % ntype,
        "authority: %s" % authority,
        "project: %s" % project,
        "tags: [%s]" % ", ".join(tags),
        "description: %s" % descr,
        "---",
        "",
        lesson,
        "",
    ]
    if location:
        # Shape (h), and the ONE place a path shaped anchor is minted. The bare
        # anchor stays in the description and the lesson above, so anchor_note_count
        # (which is measured off the exact anchor string) does not move; this line
        # adds a SECOND anchor for the same file, the one carrying its directory.
        lines.append((location_line or "This file lives at %s.") % location)
        lines.append("")
    if others:
        # Left bare on purpose: a foreign anchor written under this note's own
        # directory would claim the file lives here, and every carrier of a crowded
        # anchor would then answer the context as well as the note that owns it.
        lines.append("Files touched in the same incident: %s." % ", ".join(others))
        lines.append("")
    lines.append("What to do instead: %s." % fix)
    lines.append("")
    return "\n".join(lines)


def _write_corpus(path, rows, fingerprint, label):
    doc = {
        "schema": 1,
        "corpus": label,
        "built_by": "benchmarks/retrieval/build_vault_retrieval_fixture.py",
        "vault": "benchmarks/fixtures/vault-retrieval/vault",
        "resolution_key": "primary_path",
        "resolution_note": ("paths are relative to the fixture vault root. Note ids are "
                            "assigned at index time and are not stable, so this corpus "
                            "never keys on one."),
        "vault_fingerprint": fingerprint,
        "queries": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def build(out_dir):
    rng = random.Random(SEED)
    anchor_re = _load_anchor_regex()

    bands = list(BAND_PLAN)
    rng.shuffle(bands)
    bands += EXT_BANDS
    anchors = _anchor_names(rng, TOTAL_PRIMARY)
    homes = [_home_dir(i, anchors[i]) for i in range(TOTAL_PRIMARY)]
    if len(set(homes)) != len(homes):
        raise SystemExit("NO-DATA: two anchors share a home directory, so the crowded "
                         "band would not be winnable from the context")

    # Carrier assignment. carriers[i] is the set of NOTE KEYS mentioning anchors[i]:
    # primaries keyed "p<i>", source_of_record decoys "d<k>". The primary always
    # carries its own anchor; a mid anchor gains one decoy and a crowded anchor two,
    # which is shape (f) placed exactly where it hurts.
    # The extension block (i >= N_PRIMARY) draws its crowd from extension primaries
    # and its own source_of_record decoys "x<k>" only. A base note that gained an
    # extension anchor would have its rendered text change, which would move a tuning
    # score, so the two blocks never appear in each other's carrier sets.
    per_band_seen = {"rare": 0, "mid": 0, "crowded": 0}
    ext_band_seen = {"rare": 0, "mid": 0, "crowded": 0}
    carriers = []
    for i in range(TOTAL_PRIMARY):
        band = bands[i]
        ext = i >= N_PRIMARY
        seen_by_band = ext_band_seen if ext else per_band_seen
        j = seen_by_band[band]
        seen_by_band[band] += 1
        want = _target_count(band, j)
        pool, decoys, base = (EXT_N, EXT_DECOY, N_PRIMARY) if ext else (N_PRIMARY, N_DECOY, 0)
        letter = "x" if ext else "d"
        chosen = ["p%d" % i]
        if band in ("mid", "crowded"):
            chosen.append("%s%d" % (letter, i % decoys))
        if band == "crowded":
            second = "%s%d" % (letter, (i + 2) % decoys)
            if second not in chosen:
                chosen.append(second)
        k = 1
        while len(chosen) < want:
            cand = "p%d" % (base + ((i + 7 * k) % pool))
            if cand not in chosen:
                chosen.append(cand)
            k += 1
            if k > 4 * pool:
                raise SystemExit("NO-DATA: cannot fill carriers for anchor %d" % i)
        carriers.append(chosen)

    foreign = {}
    for i, keys in enumerate(carriers):
        for key in keys:
            if key == "p%d" % i:
                continue
            foreign.setdefault(key, []).append(anchors[i])

    notes = []          # [(relative path, text)]
    primary_path = {}   # i -> relative path inside the vault

    for i in range(TOTAL_PRIMARY):
        folder, project, ntype = _folder(i)
        anchor = anchors[i]
        others = sorted(foreign.get("p%d" % i, []))
        if i in JA_INDICES:
            k = JA_INDICES.index(i)
            name = JA_NAMES[k]
            descr = JA_DESCRIPTIONS[k].format(anchor=anchor)
            lesson = JA_BODIES[k].format(anchor=anchor)
            fix = JA_FIX
            location_line = JA_LOCATION_LINE
            lang = "ja"
        else:
            subj = SUBJECTS[i % len(SUBJECTS)]
            pred = PREDICATES[(i // len(SUBJECTS)) % len(PREDICATES)]
            name = "the %s %s" % (subj, pred)
            descr = ("The %s in %s %s, and every check downstream read the wrong number."
                     % (subj, anchor, pred))
            lesson = ("The %s lives in %s. It %s, so a run that had already lost work "
                      "still printed the shape of a clean one." % (subj, anchor, pred))
            fix = FIXES[(i * 5) % len(FIXES)]
            location_line = None
            lang = "en"
        authority = "derived" if (i % 19 == 0 and i not in JA_INDICES) else "casual"
        text = _render(
            note_id="VRB-P%03d" % i, name=name, ntype=ntype, authority=authority,
            project=project, tags=["retrieval-benchmark", "fixture", lang],
            descr=descr, lesson=lesson, fix=fix, others=others,
            location="%s/%s" % (homes[i], anchor), location_line=location_line)
        rel = "%s/vrb-p%03d-%s.md" % (folder, i, _slug(name)[:60] or "note")
        notes.append((rel, text))
        primary_path[i] = rel

    for k in range(N_DECOY):
        others = sorted(foreign.get("d%d" % k, []))
        text = _render(
            note_id="VRB-D%03d" % k, name=DECOY_NAMES[k], ntype="reference",
            authority="source_of_record", project=PROJECTS[k % 3],
            tags=["retrieval-benchmark", "fixture", "standard"],
            descr=DECOY_DESCRIPTIONS[k],
            lesson="This is the approved wording. The files below are the ones the rule "
                   "gets applied to, not the subject of this page: no line here is the "
                   "recorded lesson for any one of them.",
            fix="follow the checklist above before calling a change done",
            others=others,
            location=_decoy_home(PROJECTS[k % 3], DECOY_NAMES[k]),
            location_line="This page is kept at %s.")
        notes.append(("50-Reference/vrb-d%03d-%s.md" % (k, _slug(DECOY_NAMES[k])[:60]), text))

    for k in range(EXT_DECOY):
        others = sorted(foreign.get("x%d" % k, []))
        text = _render(
            note_id="VRB-X%03d" % k, name=EXT_DECOY_NAMES[k], ntype="reference",
            authority="source_of_record", project=PROJECTS[k % 3],
            tags=["retrieval-benchmark", "fixture", "standard"],
            descr=EXT_DECOY_DESCRIPTIONS[k],
            lesson="This is the approved wording. The files below are the ones the rule "
                   "gets applied to, not the subject of this page: no line here is the "
                   "recorded lesson for any one of them.",
            fix="follow the checklist above before calling a change done",
            others=others,
            location=_decoy_home(PROJECTS[k % 3], EXT_DECOY_NAMES[k]),
            location_line="This page is kept at %s.")
        notes.append(("50-Reference/vrb-x%03d-%s.md" % (k, _slug(EXT_DECOY_NAMES[k])[:60]), text))

    for k in range(N_UNANCHORED):
        text = _render(
            note_id="VRB-U%03d" % k, name=UNANCHORED_NAMES[k], ntype="failure",
            authority="casual", project=PROJECTS[k % 3],
            tags=["retrieval-benchmark", "fixture", "unanchored"],
            descr="A lesson with no file to hang it on, kept so the corpus is not all "
                  "anchored notes.",
            lesson=UNANCHORED_BODIES[k],
            fix="write down the thing a reader could act on, in one sentence",
            others=[])
        notes.append(("40-Failures/vrb-u%03d-%s.md" % (k, _slug(UNANCHORED_NAMES[k])[:60]), text))

    # Shape (c): the same note filed twice, identical body, different folder. Identical
    # on purpose: bm_vault.py's duplicate probe only refuses a same-stem twin whose body
    # DIFFERS, so an exact copy stays servable and the damage it does is the ordinary
    # kind (one of the two takes the slot, the other is dropped by the title dedupe).
    by_path = dict(notes)
    for i in DUPLICATED:
        rel = primary_path[i]
        notes.append(("10-Projects/gamma-site/%s" % os.path.basename(rel), by_path[rel]))
    by_path = dict(notes)

    # Measure. Anchors are read back out of the rendered text with the indexer's own
    # regex, so anchor_note_count is what bm_vault.py will index, never what was intended.
    anchor_to_notes = {}
    for rel, text in notes:
        for a in set(anchor_re.findall(text)):
            anchor_to_notes.setdefault(a, set()).add(rel)

    def band_of(n):
        if n <= 2:
            return "rare"
        return "mid" if n <= 8 else "crowded"

    queries = []
    for i, a in enumerate(anchors):
        holders = sorted(anchor_to_notes.get(a, set()))
        if primary_path[i] not in holders:
            raise SystemExit("NO-DATA: primary %s does not carry anchor %s"
                             % (primary_path[i], a))
        n = len(holders)
        if band_of(n) != bands[i]:
            raise SystemExit("NO-DATA: anchor %s measured %d notes (band %s), planned %s"
                             % (a, n, band_of(n), bands[i]))
        folder, project, ntype = _folder(i)
        queries.append({
            "qid": None,
            "query_sent": a,
            "hook_tool_input": {"file_path": "/repo/%s/%s" % (homes[i], a)},
            "folder": folder.split("/")[0],
            "project": project,
            "lang": "ja" if i in JA_INDICES else "en",
            "kind": ntype,
            "band": bands[i],
            "anchor_note_count": n,
            "primary_path": primary_path[i],
            "primary_title": _name_of(by_path[primary_path[i]]),
            "expected_paths": holders,
            "note": _why(bands[i], holders, primary_path[i], project),
        })

    tuning, heldout = [], []
    left = dict(TUNING_QUOTA)
    for q in queries:
        if left[q["band"]] > 0:
            left[q["band"]] -= 1
            q["qid"] = "VRQ%02d" % (len(tuning) + 1)
            tuning.append(q)
        else:
            q["qid"] = "VRH%02d" % (len(heldout) + 1)
            heldout.append(q)
    if len(tuning) != 40 or len(heldout) != 30:
        raise SystemExit("NO-DATA: split produced %d tuning and %d held out"
                         % (len(tuning), len(heldout)))
    ja_split = (sum(1 for q in tuning if q["lang"] == "ja"),
                sum(1 for q in heldout if q["lang"] == "ja"))
    if sum(ja_split) != len(JA_INDICES):
        raise SystemExit("NO-DATA: %d Japanese queries, expected %d"
                         % (sum(ja_split), len(JA_INDICES)))

    # Write. The vault the scorer indexes, and the stub source tree bm_freshness
    # resolves anchors against: an anchored note whose citation resolves nowhere is
    # WITHHELD as stale, which would make the whole fixture unservable.
    vault = os.path.join(out_dir, "vault")
    sources = os.path.join(out_dir, "sources")
    for d in (vault, sources):
        if os.path.isdir(d):
            shutil.rmtree(d)
    for rel, text in notes:
        p = os.path.join(vault, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    os.makedirs(sources, exist_ok=True)
    for a in sorted(anchor_to_notes):
        # Each anchor gets a stub AT ITS OWN RELATIVE PATH, so a path shaped anchor
        # resolves as the path it is and the bare basename still resolves through
        # bm_freshness's basename index. An anchor that resolved nowhere would have
        # its note WITHHELD as stale, which is a filesystem accident, not retrieval.
        stub = os.path.join(sources, *a.split("/"))
        os.makedirs(os.path.dirname(stub), exist_ok=True)
        with open(stub, "w", encoding="utf-8") as f:
            f.write("stub for the retrieval fixture: %s\n" % a)

    corpus_dir = os.path.join(REPO, "benchmarks", "retrieval")
    os.makedirs(corpus_dir, exist_ok=True)
    fingerprint = {"notes": len(notes), "anchors": len(anchor_to_notes)}
    _write_corpus(os.path.join(corpus_dir, "vault-retrieval-v1.json"), tuning,
                  fingerprint, "tuning")
    _write_corpus(os.path.join(corpus_dir, "vault-retrieval-heldout-v1.json"), heldout,
                  fingerprint, "held-out")

    print("notes %d  anchors %d  tuning %d  held out %d  japanese %d tuning / %d held out"
          % (len(notes), len(anchor_to_notes), len(tuning), len(heldout),
             ja_split[0], ja_split[1]))
    for split, rows in (("tuning", tuning), ("heldout", heldout)):
        counts = {}
        for q in rows:
            counts[q["band"]] = counts.get(q["band"], 0) + 1
        print("%s bands: %s" % (split, ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="fixture root (default: %s)" % DEFAULT_OUT)
    args = ap.parse_args(argv)
    if not os.path.isfile(TOOL):
        print("NO-DATA: bm_vault.py not found at %s" % TOOL)
        return 0
    return build(args.out)


if __name__ == "__main__":
    sys.exit(main())
