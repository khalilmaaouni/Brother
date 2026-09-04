"""test_ja_mutations: does the Japanese benchmark actually PROVE the ranking
mechanisms the code claims, or would it stay green with each one switched off?

WHY THIS EXISTS. Section 8 of the 2026-09-05 morning steering directive, in
its own words: "A perfect frozen benchmark alone is not sufficient. Deliberately
disable ... Each important mutation must make at least one relevant test fail.
If the benchmark remains green: THE BENCHMARK DOES NOT PROVE THAT MECHANISM.
Record NO-DATA rather than pretending."

So this tool takes each named mechanism, finds the switch, weight or function
that actually implements it, disables THAT (never the corpus, never a
threshold), re-runs both benchmarks in process, and reports:

  PROVEN   the mutation moved at least one case from HIT to MISS, and the
           cases are named. The benchmark is load bearing for that mechanism.
  NO-DATA  the mutation changed nothing anywhere. The benchmark does NOT
           prove that mechanism, said plainly instead of counted as a pass.
  NO-DATA  the code carries no such mechanism at all, naming what was
           searched for and what the nearest thing found was.

A MUTATION IS NOT A FIX AND NEVER TOUCHES DISK. Every mutation is a monkeypatch
applied to a freshly loaded copy of the product modules and reverted in a
finally block; the corpus files, the product files and the thresholds are read
only. The two benchmarks run through bm_vault_jbench.run_case, the same
function the scored run uses, so a mutation that flips a case here flips the
same case in the real run.

WHY THE ANALYZER IS PATCHED THROUGH _load_bm_vault_analyzer. bm_vault._search
loads a FRESH analyzer module by path on every call, so patching an analyzer
object this process already holds would be invisible to it. The documented knob
is the loader itself, which every mutation touching the analyzer replaces.

EXIT CODES. 0 whenever the run completed and every mechanism got a verdict,
whether PROVEN or NO-DATA: a NO-DATA is the honest finding this tool exists to
report, not a failure of the tool. 2 when the product tools or a corpus could
not be located at all, so an absent input can never read as a clean run.

Python 3, standard library only. No network.
No em or en dashes anywhere in this file.
"""
import importlib.util
import os
import sys

PASS, FAIL, NODATA = 0, 1, 2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IN_TREE_TOOLS_DIR = os.path.join(ROOT, "products", "brothermode", "tools")
BLIND_CORPUS = os.path.join(ROOT, "benchmarks", "ja-adversarial",
                            "adversarial-ja-corpus.json")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_tools():
    """(tools_dir, None) or (None, "NO-DATA: ..."). BROTHERMODEUP_TOOLS
    overrides, else the in-tree product directory. A candidate counts only
    when every file this tool needs is present."""
    override = os.environ.get("BROTHERMODEUP_TOOLS")
    needed = ("bm_vault_jbench.py", "bm_vault.py", "bm_vault_analyzer.py")
    for cand in ([override] if override else []) + [IN_TREE_TOOLS_DIR]:
        if cand and all(os.path.isfile(os.path.join(cand, f)) for f in needed):
            if os.path.isfile(os.path.join(cand, "fixtures",
                                            "japanese-benchmark.json")):
                return cand, None
    return None, ("NO-DATA: bm_vault_jbench.py, bm_vault.py, "
                  "bm_vault_analyzer.py and fixtures/japanese-benchmark.json "
                  "were not all found under %r; set BROTHERMODEUP_TOOLS"
                  % (override or IN_TREE_TOOLS_DIR))


class Harness(object):
    """One loaded copy of the product, plus the two corpora, plus the ability
    to score them and to say which cases missed."""

    def __init__(self, tools):
        self.tools = tools
        if tools not in sys.path:
            sys.path.insert(0, tools)
        self.jb = _load("bm_vault_jbench", os.path.join(tools, "bm_vault_jbench.py"))
        self.bm = self.jb._load_bm_vault()
        self.corpora = {}
        std, err = self.jb.load_fixture(os.path.join(
            tools, "fixtures", "japanese-benchmark.json"))
        if err:
            raise RuntimeError(err)
        self.corpora["standard"] = std
        blind, err = self.jb.load_fixture(BLIND_CORPUS)
        if err:
            raise RuntimeError(err)
        self.corpora["blind"] = blind

    def missed(self):
        """{"standard": {case ids that missed}, "blind": {...}}, scored the
        way the shipped runner scores: run_case over every case, with the
        corpus's own dictionary installed for the whole run."""
        import shutil
        import tempfile
        out = {}
        for name, fixture in self.corpora.items():
            con, stem_to_id = self.jb.build_fixture_vault(self.bm, fixture)
            vault_dir = tempfile.mkdtemp(prefix="bm-jamut-vault-")
            orig = self.bm._default_vault
            try:
                self.jb.write_dictionary(
                    vault_dir,
                    fixture.get("_meta", {}).get("dictionary_terms", []))
                self.bm._default_vault = lambda: vault_dir
                bad = set()
                for case in fixture["cases"]:
                    if not self.jb.run_case(self.bm, con, case, stem_to_id,
                                            self.jb.DEFAULT_LIMIT):
                        bad.add(case["id"])
                out[name] = bad
            finally:
                self.bm._default_vault = orig
                con.close()
                shutil.rmtree(vault_dir, ignore_errors=True)
        return out

    def token_signature(self):
        """What the analyzer produces for every query in both corpora, as one
        comparable value. Used to tell TWO different NO-DATA causes apart: a
        mutation that really did change the analyzer and simply did not change
        any SCORE (the benchmark does not depend on that mechanism), and a
        mutation that changed nothing at all (the knob itself is inert, and
        the NO-DATA says nothing about the benchmark). A control nobody drove
        backwards is a claim, so this tool drives its own controls."""
        import shutil
        import tempfile
        analyzer = self.bm._load_bm_vault_analyzer()
        sig = []
        for name in sorted(self.corpora):
            fixture = self.corpora[name]
            # The corpus's own dictionary must be installed, or a mutation of
            # the dictionary reader could not possibly show up here.
            vault_dir = tempfile.mkdtemp(prefix="bm-jamut-sig-")
            try:
                self.jb.write_dictionary(
                    vault_dir,
                    fixture.get("_meta", {}).get("dictionary_terms", []))
                for case in fixture["cases"]:
                    sig.append(tuple(analyzer.analyze(case["query"],
                                                       vault_dir=vault_dir)))
            finally:
                shutil.rmtree(vault_dir, ignore_errors=True)
        return tuple(sig)

    def patched_analyzer_loader(self, mutate):
        """Replace bm_vault's analyzer loader with one that loads a fresh
        analyzer and then applies MUTATE to it. Returns the original loader so
        the caller can restore it."""
        orig = self.bm._load_bm_vault_analyzer

        def loader():
            mod = orig()
            mutate(mod)
            return mod

        self.bm._load_bm_vault_analyzer = loader
        return orig


# Each entry: (mechanism name, what implements it, a callable taking the
# Harness and returning a restore callable, or None when the code carries no
# such mechanism, in which case the third field is the NO-DATA sentence).
def _mut_entity_type_conflict(h):
    return None


def _mut_contradiction_penalty(h):
    """R2's attribute-CONFLICT arm in bm_vault._ja_disambiguate: two confusable
    entities each owning an attribute the other lacks. Disabled by making every
    candidate's attribute set identical, which is the state in which the arm
    can never see a conflict."""
    bm = h.bm
    orig = bm._ja_content_tok
    bm._ja_content_tok = lambda t: False
    def restore():
        bm._ja_content_tok = orig
    return restore


def _mut_relationship_role_conflict(h):
    """_JA_REL_MARKERS: the words that mark a query as asking about a
    RELATIONSHIP, which switches R1's sibling exclusion off so a genuine
    affiliate is not dropped as a decoy. Emptied here."""
    bm = h.bm
    orig = bm._JA_REL_MARKERS
    bm._JA_REL_MARKERS = ()
    def restore():
        bm._JA_REL_MARKERS = orig
    return restore


def _mut_exact_name_authority(h):
    """bm_vault._ja_query_named: the company name the query writes with a legal
    form glued to it, which is what tells the ranker "the asker NAMED this"
    apart from "this candidate fits the description". Made to find nothing."""
    bm = h.bm
    orig = bm._ja_query_named
    bm._ja_query_named = lambda qnorm: []
    def restore():
        bm._ja_query_named = orig
    return restore


def _mut_alias_authority(h):
    """The dictionary an alias is declared in. bm_vault_analyzer's
    load_dictionaries is what turns a declared name into ONE segmentation term
    instead of the generic bigrams every peer shares, so emptying it removes
    every alias the vault declares while leaving the text untouched. This is
    deliberately the WIDEST alias knob available: an A=B identity alias
    (E121's parse_alias) reaches retrieval only through these same two files,
    so emptying them disables the alias reader too, and a NO-DATA here is a
    NO-DATA for every alias mechanism the analyzer has."""
    def mutate(mod):
        mod.load_dictionaries = lambda vault_dir: ([], [])
    return _restorer(h, mutate)


def _mut_identity_token_weighting(h):
    """_JA_GENERIC plus the two IDF thresholds: what stops a word every company
    note carries (会社, 代表, 本社) from being read as a distinguishing
    attribute. Emptied and zeroed, so every token weighs as identity bearing."""
    bm = h.bm
    orig_generic, orig_attr, orig_name = (
        bm._JA_GENERIC, bm._JA_ATTR_IDF, bm._JA_NAME_IDF)
    bm._JA_GENERIC = frozenset()
    bm._JA_ATTR_IDF = 0.0
    bm._JA_NAME_IDF = 0.0
    def restore():
        bm._JA_GENERIC = orig_generic
        bm._JA_ATTR_IDF = orig_attr
        bm._JA_NAME_IDF = orig_name
    return restore


def _mut_both_sides_normalization(h):
    """JA78 mechanism 2: bm_vault._cjk_hits folds the NOTE side with the same
    analyzer.normalize the query side gets. Disabled by making normalize the
    identity, which is the one sided state this repository shipped before."""
    def mutate(mod):
        mod.normalize = lambda t: t or ""
    return _restorer(h, mutate)


def _mut_romaji_reading(h):
    """JA78 mechanism 1: analyzer.romaji_to_kana, which reads a Latin token as
    the kana it romanises so a romanised company name reaches its own note.
    Disabled by declining every word."""
    def mutate(mod):
        if not hasattr(mod, "romaji_to_kana"):
            raise AttributeError("romaji_to_kana")
        mod.romaji_to_kana = lambda word: ""
        mod.reading_variants = lambda toks: []
    return _restorer(h, mutate)


def _restorer(h, mutate):
    orig = h.patched_analyzer_loader(mutate)
    def restore():
        h.bm._load_bm_vault_analyzer = orig
    return restore


#: Mechanisms whose knob lives in the ANALYZER, so token_signature() can say
#: whether the knob bit at all when a mutation changes no score.
_ANALYZER_LEVEL = frozenset((
    "alias authority",
    "both-sides normalization (JA78)",
    "romaji reading (JA78)",
))

MECHANISMS = (
    ("entity-type conflict", None,
     "no entity-type mechanism exists in the ranking path: bm_vault._search "
     "and _ja_disambiguate carry no entity-type vocabulary at all, and the "
     "nearest thing found is _ja_disambiguate's info[...]['entity'] flag, "
     "which is a name-length heuristic for IS this an entity, never a TYPE "
     "that could conflict with another. The ENTITY_TYPES vocabulary in "
     "products/brothermode/tools/bm_vault_entity.py is a corpus check tool "
     "and is never consulted by retrieval"),
    ("contradiction penalty", _mut_contradiction_penalty, None),
    ("relationship-role conflict", _mut_relationship_role_conflict, None),
    ("exact-name authority", _mut_exact_name_authority, None),
    ("alias authority", _mut_alias_authority, None),
    ("identity-bearing-token weighting", _mut_identity_token_weighting, None),
    ("both-sides normalization (JA78)", _mut_both_sides_normalization, None),
    ("romaji reading (JA78)", _mut_romaji_reading, None),
)


def main(argv=None):
    tools, err = find_tools()
    if err:
        print(err)
        return NODATA
    try:
        h = Harness(tools)
    except RuntimeError as e:
        print("NO-DATA: %s" % e)
        return NODATA

    baseline = h.missed()
    baseline_signature = h.token_signature()
    print("baseline misses: standard %d, blind %d"
          % (len(baseline["standard"]), len(baseline["blind"])))
    print("")

    proven = 0
    nodata = 0
    for name, mutator, absent_reason in MECHANISMS:
        if mutator is None:
            print("NO-DATA: the benchmark does not prove %s" % name)
            print("         %s." % absent_reason)
            print("")
            nodata += 1
            continue
        try:
            restore = mutator(h)
        except AttributeError as e:
            print("NO-DATA: the benchmark does not prove %s" % name)
            print("         the code carries no %s to disable." % e)
            print("")
            nodata += 1
            continue
        try:
            after = h.missed()
            knob_bit = (h.token_signature() != baseline_signature
                        if name in _ANALYZER_LEVEL else None)
        finally:
            restore()
        flipped = {c: sorted(after[c] - baseline[c]) for c in after}
        total = sum(len(v) for v in flipped.values())
        if total:
            proven += 1
            print("PROVEN: %s" % name)
            for corpus in sorted(flipped):
                if flipped[corpus]:
                    print("        disabling it makes %s miss %s"
                          % (corpus, ", ".join(flipped[corpus])))
        else:
            nodata += 1
            print("NO-DATA: the benchmark does not prove %s" % name)
            print("         disabling it moved no case from hit to miss in "
                  "either corpus.")
            if knob_bit is True:
                print("         the knob itself DID bite: the analyzer's own "
                      "token output changed under the mutation, so this is a "
                      "gap in the benchmark, not a dead switch.")
            elif knob_bit is False:
                print("         and the knob changed nothing observable "
                      "either, so this NO-DATA says nothing about the "
                      "benchmark: the switch is inert here.")
        print("")

    # A mutation must never be able to leave the product mutated: re-score and
    # refuse to report anything if the baseline did not come back.
    final = h.missed()
    if final != baseline:
        print("FAIL: the baseline did not restore after mutation "
              "(standard %d, blind %d); every verdict above is suspect"
              % (len(final["standard"]), len(final["blind"])))
        return FAIL

    print("mechanisms PROVEN by the benchmark: %d" % proven)
    print("mechanisms reported NO-DATA:        %d" % nodata)
    print("baseline restored after every mutation: yes")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
