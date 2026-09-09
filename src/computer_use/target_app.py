from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/demo")

MEMBERS = {
    "10001": {"name": "Alex Example", "status": "Active", "balance": "$12,345.67"},
    "10002": {"name": "Jordan Sample", "status": "Inactive", "balance": "$98.76"},
}


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html><head><title>{escape(title)}</title><style>
body {{ font-family: Arial, sans-serif; background: #e7e4d7; margin: 0; color: #172036; }}
header {{ background: #163a63; color: white; padding: 12px 24px; }}
main {{ margin: 20px auto; max-width: 900px; background: #fff; border: 3px ridge #aaa; padding: 18px; }}
table {{ border-collapse: collapse; width: 100%; }} td, th {{ border: 1px solid #777; padding: 8px; }}
.error {{ color: #9d1616; background: #fee; border: 1px solid #d88; padding: 10px; }}
.toolbar {{ background: #d6d6d6; padding: 8px; margin-bottom: 12px; }}
button, input {{ font: inherit; padding: 5px 8px; }}
</style></head><body><header><strong>Northstar Core - Training Environment</strong></header>
<main>{body}</main></body></html>"""
    )


@router.get("", response_class=HTMLResponse)
async def demo_home() -> HTMLResponse:
    return page(
        "Northstar Core",
        """<div class="toolbar">Operations &gt; Member Service</div>
<h1>Member Service Console</h1>
<p>This synthetic application intentionally resembles a legacy back-office system.</p>
<iframe name="legacy-main" title="Member search workspace" src="/demo/search"
        style="width:100%;height:420px;border:2px inset #aaa"></iframe>""",
    )


@router.get("/search", response_class=HTMLResponse)
async def search_form() -> HTMLResponse:
    return page(
        "Member Search",
        """<h2>Member Lookup</h2>
<form method="post" action="/demo/search">
<table><tr><td><label for="member-number">Member Number</label></td>
<td><input id="member-number" name="member_id" autocomplete="off"></td></tr></table>
<p><button type="submit">Search Records</button></p>
</form>
<small>Training data only. Try 10001, 99999, 40300, or 40800.</small>""",
    )


@router.post("/search", response_class=HTMLResponse)
async def search_result(member_id: str = Form(...)) -> HTMLResponse:
    member_id = member_id.strip()
    if member_id == "40300":
        return page("Permission Denied", '<div class="error">Permission denied for this member.</div>')
    if member_id == "40800":
        return page("Session Expired", '<div class="error">Your session has expired. Sign in again.</div>')
    member = MEMBERS.get(member_id)
    if member is None:
        return page(
            "No Member Found",
            '<div class="error">No member found for the supplied member number.</div>'
            '<p><a href="/demo/search">Return to search</a></p>',
        )
    return page(
        "Search Results",
        f"""<h2>Search Results</h2><table><tr><th>Member</th><th>Name</th><th>Action</th></tr>
<tr><td>{escape(member_id)}</td><td>{escape(member['name'])}</td>
<td><a href="/demo/member/{escape(member_id)}">Open Member</a></td></tr></table>""",
    )


@router.get("/member/{member_id}", response_class=HTMLResponse)
async def member_detail(member_id: str) -> HTMLResponse:
    member = MEMBERS.get(member_id)
    if member is None:
        return page("No Member Found", '<div class="error">No member found.</div>')
    return page(
        "Member Detail",
        f"""<div class="toolbar">Member &gt; Accounts</div><h2>Member Account Summary</h2>
<table><tr><th>Member</th><td>{escape(member_id)}</td></tr>
<tr><th>Name</th><td>{escape(member['name'])}</td></tr>
<tr><th>Status</th><td id="member-status">{escape(member['status'])}</td></tr></table>
<h3>Savings Account</h3><table><tr><th>Account</th><th>Current Balance</th></tr>
<tr><td>Primary Savings</td><td id="savings-balance">{escape(member['balance'])}</td></tr></table>
<p><a href="/demo/subaccount/{escape(member_id)}">Open New Sub-Account</a></p>""",
    )


@router.get("/subaccount/{member_id}", response_class=HTMLResponse)
async def subaccount(member_id: str) -> HTMLResponse:
    return page(
        "New Sub-Account",
        f"""<h2>New Sub-Account</h2><p>Member: {escape(member_id)}</p>
<form><label>Nickname <input name="nickname"></label>
<p><button type="button" onclick="document.getElementById('confirm').hidden=false">Review</button></p>
<section id="confirm" hidden><h3>Confirmation</h3>
<p>This would create a new account. Human approval is required.</p>
<button type="button">Create Account</button></section></form>""",
    )

