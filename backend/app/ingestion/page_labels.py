"""The page number printed on the page, as against the one we counted.

Pagination here is sequential from the first page of the document. The
document prints its own numbers, and the two need not agree: a cover page,
a table of contents or an exhibit index is counted but not numbered, so the
printed label runs behind the index by however many such leaves there are.

Measured across the practice corpus: 3M's 2018 10-K and Amazon's 2017 10-K
agree exactly, while JPMorgan's 2022 10-K is offset by two - the figure we
cite as "page 291" is printed "289" in the document the reader is holding.
Citing a number they cannot find is the same class of failure as citing a
quote they cannot find, which the verifier already prevents.

The offset is *detected* rather than assumed, and by majority: a filing's
statement pages routinely end in a figure that looks like a page label, so
no single page is trusted. Where a document prints nothing at all the
offset is zero and the index stands, which is the behaviour that existed
before this module.
"""

from __future__ import annotations

import re
from collections import Counter

# A number alone at the very end of a page, which is where a footer sits.
_TRAILING_NUMBER_RE = re.compile(r"(?:^|\s)(\d{1,4})\s*$")

# Beyond this the "label" is a data cell that happens to end the page, not a
# page number: no filing's printed numbering runs hundreds of pages away
# from its own sequence.
_MAX_PLAUSIBLE_OFFSET = 40

# Enough pages must agree before the offset is believed. Below this it is
# more likely a coincidence among trailing figures than real numbering.
_MIN_AGREEING_PAGES = 3


def detect_offset(pages: list[str]) -> int:
    """How far the printed label runs from the sequential index.

    Returns `label - index`, so a document numbered from its third leaf
    gives -2. Zero when nothing is printed or nothing agrees, which leaves
    the index in use.
    """
    votes: Counter[int] = Counter()
    for index, text in enumerate(pages, 1):
        match = _TRAILING_NUMBER_RE.search((text or "").strip())
        if not match:
            continue
        offset = int(match.group(1)) - index
        if abs(offset) <= _MAX_PLAUSIBLE_OFFSET:
            votes[offset] += 1

    if not votes:
        return 0
    offset, agreeing = votes.most_common(1)[0]
    return offset if agreeing >= _MIN_AGREEING_PAGES else 0


def label_for(page: int, offset: int) -> int:
    """The number printed on `page`, or the page itself when that is absurd.

    An offset that would put the label at zero or below cannot be right for
    this page - it is the front matter, which the numbering has not reached
    - so the index is shown rather than a number no reader could match.
    """
    label = page + offset
    return label if label >= 1 else page
