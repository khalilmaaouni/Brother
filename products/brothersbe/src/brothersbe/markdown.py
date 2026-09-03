"""The one Markdown subset renderer this repository ships.

WHY THIS EXISTS AS A MODULE. The renderer below was written inside
`docs/book/build_book.py` and lived there alone. `src/brothersbe/book.py`
needs the identical subset (the field book and the eighteen-chapter book are
the same markdown dialect, written by the same hands, read by the same
people), and copying it would have produced two renderers free to diverge:
a code span escaped one way here and another way there, a table parsed in one
book and printed as literal pipes in the other. That is the exact drift the
field book exists to remove, so reproducing it in the field book's own
implementation was not an option.

The behaviour is unchanged from the version that shipped inside
`build_book.py`, which now imports from here. `tools/test_sbe_book.py` builds
the eighteen-chapter book and asserts every chapter is present, so a
regression in this move goes red there.

THE SUBSET, stated so nobody has to reverse-engineer it: ATX headings, fenced
code blocks (with `mermaid` fences emitted as `<pre class="mermaid">` for a
client-side renderer to pick up), pipe tables with a separator row, unordered
and ordered lists, paragraphs, and inline code, bold and links. There is no
blockquote, no image, no nested list, no HTML passthrough and no footnote.
A construct outside the subset renders as the literal text it is, which is
visible in the output rather than silently dropped.

ESCAPING ORDER. `render_inline` escapes the raw text FIRST and only then adds
markup, so the tags this module writes are never themselves escaped, and text
that came from a file is never able to inject a tag. Code spans are stashed
before the bold and link passes run, because a path like `**/*.py` or an
identifier carrying underscores is code, not emphasis.
"""
import re

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_OPEN_RE = re.compile(r"^```\s*(\S*)\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")
UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
CODE_RE = re.compile(r"`([^`\n]+)`")


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_inline(text):
    """Escape then apply bold and link markup. Order matters: HTML escaping
    runs first, on raw text only, so the tags this function adds afterward
    are never themselves escaped."""
    text = esc(text)
    # Code spans first, and their contents are shielded from the markup passes
    # that follow: a path like `**/*.py` or a command carrying an underscore is
    # code, not emphasis, and a reader who sees literal backticks in a book
    # about precision loses confidence in every other line on the page.
    spans = []

    def _stash(match):
        spans.append(match.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    text = CODE_RE.sub(_stash, text)
    text = BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = LINK_RE.sub(
        lambda m: '<a href="%s">%s</a>' % (m.group(2).replace('"', "&quot;"), m.group(1)),
        text)
    for index, body in enumerate(spans):
        text = text.replace("\x00%d\x00" % index, "<code>%s</code>" % body)
    return text


def markdown_to_html(text):
    """Convert one chapter's Markdown subset to HTML. Returns (title, html):
    title is the raw text of the first level-1 heading, undecorated, for the
    caller to use as the chapter's nav label and id."""
    lines = text.split("\n")
    i, n = 0, len(lines)
    out = []
    title = None
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = HEADING_RE.match(line)
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if title is None and level == 1:
                title = heading
            out.append("<h%d>%s</h%d>" % (level, render_inline(heading), level))
            i += 1
            continue
        m = FENCE_OPEN_RE.match(line)
        if m:
            lang = m.group(1)
            i += 1
            code_lines = []
            while i < n and not FENCE_CLOSE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1
            code = esc("\n".join(code_lines))
            if lang == "mermaid":
                out.append('<pre class="mermaid">%s</pre>' % code)
            elif lang:
                out.append('<pre><code class="language-%s">%s</code></pre>' % (lang, code))
            else:
                out.append("<pre><code>%s</code></pre>" % code)
            continue
        if line.lstrip().startswith("|") and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "<tr>%s</tr>" % "".join("<th>%s</th>" % render_inline(c) for c in header)
            tbody = "".join(
                "<tr>%s</tr>" % "".join("<td>%s</td>" % render_inline(c) for c in row)
                for row in rows)
            out.append("<table><thead>%s</thead><tbody>%s</tbody></table>" % (thead, tbody))
            continue
        ordered = OL_RE.match(line) is not None
        unordered = UL_RE.match(line) is not None
        if ordered or unordered:
            items = []
            while i < n:
                om, um = OL_RE.match(lines[i]), UL_RE.match(lines[i])
                if ordered and om:
                    items.append(om.group(1))
                    i += 1
                elif unordered and um:
                    items.append(um.group(1))
                    i += 1
                else:
                    break
            tag = "ol" if ordered else "ul"
            body = "".join("<li>%s</li>" % render_inline(it) for it in items)
            out.append("<%s>%s</%s>" % (tag, body, tag))
            continue
        para = []
        while (i < n and lines[i].strip() and not HEADING_RE.match(lines[i])
               and not FENCE_OPEN_RE.match(lines[i]) and not UL_RE.match(lines[i])
               and not OL_RE.match(lines[i]) and not lines[i].lstrip().startswith("|")):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % render_inline(" ".join(para)))
    return title, "\n".join(out)
