"""Generate samples/sample-prd.pdf — the test input for this submission.

Committed so the test input is reproducible rather than an opaque binary: anyone can read the PRD
text here, regenerate the PDF, and re-run the planner against it.

    python tools/make_sample_prd.py

The subject ("SplitTab", a group expense-splitting app) is deliberately ordinary and has enough
real dependency depth — auth before groups, groups before expenses, expenses before settlement,
settlement before export — to show that the computed ordering is doing actual work.
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

OUT = Path(__file__).resolve().parent.parent / "samples" / "sample-prd.pdf"

TITLE = "SplitTab - Product Requirements Document"

SECTIONS: list[tuple[str, list[str]]] = [
    (
        "1. Overview",
        [
            "SplitTab is a mobile-first web application for splitting shared expenses within a "
            "small group - flatmates, a holiday party, a recurring dinner club. A member records "
            "an expense once, SplitTab keeps a running balance for everyone, and at any point a "
            "member can ask for the minimum set of payments that clears all debts in the group.",
            "The product is deliberately narrow. It does not move money, it does not support "
            "multiple currencies in v1, and it has no social feed. Its value is that the "
            "arithmetic is always right and always visible.",
        ],
    ),
    (
        "2. Users",
        [
            "Member - anyone who has joined a group. Can add expenses, view balances, and settle up.",
            "Group owner - the member who created the group. Can invite and remove members and "
            "rename the group. There is exactly one owner and ownership can be transferred.",
        ],
    ),
    (
        "3. Functional requirements",
        [
            "FR-1 Accounts. A user signs up with email and password, and signs in to receive a "
            "session. Passwords are stored hashed. Sessions expire after 30 days.",
            "FR-2 Groups. A signed-in user can create a group with a name and a base currency. "
            "The creator becomes the owner and the first member.",
            "FR-3 Invitations. An owner can invite a member by email. The invitation produces a "
            "single-use link valid for 7 days. Accepting it adds the user to the group.",
            "FR-4 Expenses. A member records an expense with a description, an amount, a date, a "
            "payer, and the set of members it is shared between. An expense may be split equally, "
            "by exact amounts, or by percentage shares. Splits must sum to the expense total; a "
            "split that does not sum is rejected with an explanation of the difference.",
            "FR-5 Editing. Any member of the group can edit or delete an expense. Every change is "
            "recorded in an immutable audit trail showing who changed what and when. Balances "
            "recompute after any edit.",
            "FR-6 Balances. Each member sees, for every group, what they owe and what they are "
            "owed, per counterparty. The sum of all balances in a group is always zero.",
            "FR-7 Settlement. A member can ask for a settlement plan: the minimum number of "
            "payments that brings every balance to zero. Recording a settlement payment updates "
            "the balances the same way an expense does.",
            "FR-8 Notifications. A member receives an email when they are added to a group, when "
            "an expense involving them is recorded, and a weekly digest of their open balances. "
            "Each of the three is individually switchable in the member's settings.",
            "FR-9 Export. A member can export a group's expense history as CSV, covering an "
            "optional date range, with one row per expense and one column per member's share.",
            "FR-10 Activity view. A group has a reverse-chronological activity feed of expenses, "
            "edits, settlements, and membership changes, paged at 50 entries.",
        ],
    ),
    (
        "4. Non-functional requirements",
        [
            "NFR-1 Money is never stored or computed in floating point. All amounts are integer "
            "minor units, and every split must reconcile to the cent.",
            "NFR-2 A group of 20 members with 5,000 expenses loads its balance view in under 500 "
            "ms at the 95th percentile.",
            "NFR-3 Every write is authorised against group membership. A user must not be able to "
            "read or alter a group they do not belong to, including by guessing an id.",
            "NFR-4 The settlement algorithm is covered by property-based tests asserting that the "
            "resulting balances are all zero and that no member pays more than they owed.",
        ],
    ),
    (
        "5. Out of scope for v1",
        [
            "Payment processing, multi-currency groups, receipt image OCR, recurring expenses, and "
            "native mobile applications.",
        ],
    ),
]


def build() -> Path:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 17)
    pdf.multi_cell(0, 9, TITLE)
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(0, 6, "Version 1.0 - draft for engineering")
    pdf.ln(4)

    for heading, paragraphs in SECTIONS:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, heading)
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 11)
        for paragraph in paragraphs:
            pdf.multi_cell(0, 6, paragraph)
            pdf.ln(2)
        pdf.ln(2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
