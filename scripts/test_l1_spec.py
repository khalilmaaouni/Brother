import os
import re
import sys
import tempfile
import unittest

REAL_SPEC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'docs', 'architecture', 'ANTIGRAVITY-ADAPTER-SPEC.md'))

ALLOWED_PREFIX = "https://antigravity.google/docs/"

URL_RE = re.compile(r'https?://[^\s\)\]\}"\']+')
PATH_RE = re.compile(r'(?:~?\.?/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+')

def _read_spec(spec_path):
    if not isinstance(spec_path, str):
        return None, "NO-DATA: spec_path must be a string"
    try:
        with open(spec_path, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        return None, "NO-DATA: spec file missing"
    except IsADirectoryError:
        return None, "NO-DATA: spec path is a directory"
    except OSError:
        return None, "NO-DATA: spec file unreadable"
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None, "NO-DATA: spec file is not valid UTF-8"
    return text, None

def verify_spec_urls(spec_path: str) -> tuple[bool, list[str]]:
    text, err = _read_spec(spec_path)
    if err:
        return False, [err]
    urls = URL_RE.findall(text)
    if not urls:
        return False, ["NO-DATA: no URLs found in spec"]
    bad = []
    for url in urls:
        url = url.rstrip('.,;:')
        if not url.startswith(ALLOWED_PREFIX):
            bad.append(f"DISALLOWED URL: {url}")
    if bad:
        return False, bad
    return True, []

def _check_claim(line):
    errors = []
    urls = URL_RE.findall(line)
    if urls:
        for url in urls:
            url_clean = url.rstrip('.,;:')
            if not url_clean.startswith(ALLOWED_PREFIX):
                errors.append(f"DISALLOWED URL: {url_clean}")
        if errors:
            return errors
        return []
    if PATH_RE.search(line):
        return []
    errors.append(f"NO-DATA: uncited claim: {line}")
    return errors

def verify_no_inferred_points(spec_path: str) -> tuple[bool, list[str]]:
    text, err = _read_spec(spec_path)
    if err:
        return False, [err]
    lines = text.splitlines()
    errors = []
    in_facts = False
    found_facts_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            if in_facts:
                in_facts = False
            if 'facts' in stripped.lower():
                in_facts = True
                found_facts_section = True
            continue
        if stripped.startswith('---'):
            if in_facts:
                in_facts = False
            continue
        if in_facts:
            if not stripped:
                continue
            claim = stripped
            if claim.startswith('- '):
                claim = claim[2:].strip()
            elif claim.startswith('* '):
                claim = claim[2:].strip()
            if not claim:
                continue
            errors.extend(_check_claim(claim))
    if not found_facts_section:
        return False, ["NO-DATA: no facts section found"]
    if errors:
        return False, errors
    return True, []

class TestCitationAudit(unittest.TestCase):
    def _write_temp(self, content):
        fd, path = tempfile.mkstemp(suffix='.md', text=True)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    @unittest.skipUnless(os.path.isfile(REAL_SPEC), reason="real spec not found")
    def test_urls_allowlisted(self):
        ok, errors = verify_spec_urls(REAL_SPEC)
        self.assertTrue(ok, f"real spec failed: {errors}")
        allowed = self._write_temp("See https://antigravity.google/docs/hooks for info.\n")
        ok, errors = verify_spec_urls(allowed)
        self.assertTrue(ok, errors)
        disallowed = self._write_temp("See https://example.com/bad for info.\n")
        ok, errors = verify_spec_urls(disallowed)
        self.assertFalse(ok)
        self.assertTrue(any("DISALLOWED" in e for e in errors))

    @unittest.skipUnless(os.path.isfile(REAL_SPEC), reason="real spec not found")
    def test_no_inferred_claim(self):
        ok, errors = verify_no_inferred_points(REAL_SPEC)
        self.assertTrue(ok, f"real spec has uncited claims: {errors}")
        cited = self._write_temp("## Facts a builder needs\n- See docs/architecture/foo.md for details.\n")
        ok, errors = verify_no_inferred_points(cited)
        self.assertTrue(ok, errors)
        uncited = self._write_temp("## Facts a builder needs\n- This is a claim without citation.\n")
        ok, errors = verify_no_inferred_points(uncited)
        self.assertFalse(ok)
        self.assertTrue(any("NO-DATA" in e for e in errors))

    def test_empty_urls_fail(self):
        path = self._write_temp("No URLs here.\n")
        ok, errors = verify_spec_urls(path)
        self.assertFalse(ok)
        self.assertTrue(any("no URLs" in e for e in errors))

    def test_hostile_inputs(self):
        bad_inputs = [None, 123, 4.56, True, b'bytes', '/nonexistent/path', tempfile.gettempdir()]
        for bad in bad_inputs:
            ok, errors = verify_spec_urls(bad)
            self.assertFalse(ok, f"verify_spec_urls accepted {bad!r}")
            self.assertTrue(errors, f"no error for {bad!r}")
            ok, errors = verify_no_inferred_points(bad)
            self.assertFalse(ok, f"verify_no_inferred_points accepted {bad!r}")
            self.assertTrue(errors, f"no error for {bad!r}")

    def test_rerun_idempotent(self):
        path = self._write_temp("https://antigravity.google/docs/hooks\n")
        res1 = verify_spec_urls(path)
        res2 = verify_spec_urls(path)
        self.assertEqual(res1, res2)

    def test_renamed_facts_section_blocks(self):
        spec = self._write_temp("## Builder Facts\n- This is a claim without citation.\n")
        ok, errors = verify_no_inferred_points(spec)
        self.assertFalse(ok)
        self.assertTrue(any("NO-DATA" in e for e in errors))

    def test_disallowed_url_claim_blocks(self):
        spec = self._write_temp("## Facts a builder needs\n- See https://example.com/bad for details.\n")
        ok, errors = verify_no_inferred_points(spec)
        self.assertFalse(ok)
        self.assertTrue(any("DISALLOWED URL" in e for e in errors))

    def test_no_facts_section_blocks(self):
        spec = self._write_temp("# Some heading\n- This is a claim without citation.\n")
        ok, errors = verify_no_inferred_points(spec)
        self.assertFalse(ok)
        self.assertTrue(any("no facts section" in e for e in errors))

    def test_uncited_nonbullet_blocks(self):
        spec = self._write_temp("## Facts a builder needs\nThis is a claim without citation.\n")
        ok, errors = verify_no_inferred_points(spec)
        self.assertFalse(ok)
        self.assertTrue(any("NO-DATA" in e for e in errors))

    def test_uncited_outside_facts_blocks(self):
        spec = self._write_temp("## Facts a builder needs\n- See docs/architecture/foo.md for details.\n\n## More Facts\n- This is a claim without citation.\n")
        ok, errors = verify_no_inferred_points(spec)
        self.assertFalse(ok)
        self.assertTrue(any("NO-DATA" in e for e in errors))

if __name__ == "__main__":
    unittest.main()
