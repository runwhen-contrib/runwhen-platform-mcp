"""RunWhen-styled consent page for FastMCP's OAuth proxy.

Replaces FastMCP's default ``create_consent_html`` with a page that
matches RunWhen's split-panel login design (Inter font, design tokens,
RunWhen logo, dark branding panel).
"""

from __future__ import annotations

import html as html_module
from typing import Any

_RUNWHEN_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 150 150">'
    '<path d="M0 0 C49.5 0 99 0 150 0 C150 49.5 150 99 150 150 C100.5 150 51 150 0 150'
    ' C0 100.5 0 51 0 0Z" fill="#1F8BF0"/>'
    '<path d="M0 0 C4.228 3.278 7.023 7.109 9.398 11.859 C10.166 20.626 9.228 27.609 3.648 34.547'
    " C-2.915 41.589-10.449 44.953-19.602 47.859 C-20.592 47.859-21.582 47.859-22.602 47.859"
    " C-19.563 58.248-19.563 58.248-12.602 65.859 C-7.252 66.942-2.916 66.432 1.773 63.609"
    " C4.037 62.021 5.698 60.535 7.586 58.484 C8.184 57.948 8.782 57.412 9.398 56.859"
    " C12.148 57.109 12.148 57.109 14.398 57.859 C14.482 61.218 13.882 63.284 11.715 65.863"
    " C5.481 71.719-2.124 76.902-10.914 77.422 C-17.162 76.469-21.155 72.942-25.078 68.199"
    " C-28.221 63.373-32.602 55.681-32.602 49.859 C-33.262 49.859-33.922 49.859-34.602 49.859"
    " C-34.737 50.491-34.872 51.123-35.012 51.773 C-37 60.037-40.226 70.865-46.664 76.742"
    " C-49.194 78.201-50.727 78.302-53.602 77.859 C-55.414 76.734-55.414 76.734-56.602 74.859"
    " C-57.354 69.056-55.145 65.328-52.414 60.422 C-51.972 59.609-51.53 58.797-51.075 57.959"
    " C-50.111 56.195-49.14 54.435-48.164 52.677 C-46.532 49.734-44.928 46.776-43.324 43.816"
    " C-42.21 41.788-41.094 39.761-39.977 37.734 C-39.463 36.788-38.95 35.841-38.421 34.865"
    " C-31.239 22.026-31.239 22.026-25.602 17.859 C-22.857 18.125-22.857 18.125-20.602 18.859"
    " C-20.91 26.714-24.132 32.9-27.602 39.859 C-19.088 38.157-12.098 35.607-7.102 28.172"
    " C-4.156 23.304-2.826 19.652-3.602 13.859 C-5.278 10.507-6.44 8.467-9.789 6.715"
    " C-16.489 4.677-25.548 4.799-32.227 7.047 C-38.665 10.504-42.041 15.722-46.051 21.652"
    " C-47.602 23.859-47.602 23.859-49.602 25.859 C-52.602 26.109-52.602 26.109-55.602 25.859"
    " C-57.602 23.859-57.602 23.859-57.977 20.109 C-57.775 13.902-54.329 10.385-49.977 6.297"
    ' C-36.626-4.941-15.529-9.135 0 0Z" fill="#F8FBFE" transform="translate(74.602,37.141)"/>'
    '<path d="M0 0 C1.207 0.031 1.207 0.031 2.438 0.063 C5.59 6.017 8.044 12.085 10.387 18.402'
    " C11.187 20.945 11.187 20.945 12.438 22.063 C12.675 21.432 12.912 20.801 13.156 20.151"
    " C14.226 17.308 15.301 14.466 16.375 11.625 C16.935 10.135 16.935 10.135 17.506 8.615"
    " C17.864 7.671 18.221 6.727 18.59 5.754 C19.085 4.442 19.085 4.442 19.59 3.104"
    " C20.438 1.063 20.438 1.063 21.438 0.063 C23.104 0.022 24.771 0.02 26.438 0.063"
    " C26.706 4.782 25.936 7.976 24.066 12.301 C23.329 14.025 23.329 14.025 22.576 15.783"
    " C22.056 16.968 21.536 18.154 21 19.375 C20.484 20.58 19.967 21.784 19.436 23.025"
    " C15.611 31.889 15.611 31.889 14.438 33.063 C12.771 33.103 11.104 33.105 9.438 33.063"
    " C6.226 26.139 3.2 19.178 0.438 12.063 C-1.657 14.157-2.231 15.432-3.254 18.16"
    " C-3.559 18.961-3.864 19.762-4.178 20.588 C-4.49 21.425-4.803 22.262-5.125 23.125"
    " C-5.749 24.783-6.375 26.441-7.004 28.098 C-7.279 28.834-7.554 29.57-7.838 30.328"
    " C-8.563 32.063-8.563 32.063-9.563 33.063 C-11.229 33.103-12.896 33.105-14.563 33.063"
    " C-16.253 29.741-17.913 26.405-19.563 23.063 C-19.904 22.399-20.246 21.735-20.598 21.051"
    " C-22.549 17.012-22.928 14.353-21.563 10.063 C-20.573 9.073-19.583 8.083-18.563 7.063"
    " C-16.583 12.013-14.603 16.963-12.563 22.063 C-10.897 19.565-9.913 17.627-8.871 14.867"
    " C-8.566 14.069-8.261 13.271-7.947 12.449 C-7.635 11.62-7.322 10.791-7 9.938"
    " C-6.376 8.288-5.75 6.64-5.121 4.992 C-4.846 4.262-4.571 3.533-4.287 2.781"
    ' C-3.158 0.102-3.041 0.074 0 0Z" fill="#E9C652" transform="translate(106.563,58.938)"/>'
    "</svg>"
)


def create_consent_html(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Application Access Request",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
    csp_policy: str | None = None,
    is_cimd_client: bool = False,
    cimd_domain: str | None = None,
    **_extra: Any,
) -> str:
    """RunWhen-branded replacement for FastMCP's default consent page."""
    client_display = html_module.escape(client_name or client_id)
    server_display = html_module.escape(server_name or "RunWhen Platform")
    redirect_escaped = html_module.escape(redirect_uri)
    scopes_display = ", ".join(html_module.escape(s) for s in scopes) if scopes else "openid"

    logo_56 = _RUNWHEN_LOGO_SVG.format(size="56")
    logo_48 = _RUNWHEN_LOGO_SVG.format(size="48")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize &mdash; RunWhen</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"
      rel="stylesheet">
<style>
  *,*::before,*::after {{ margin:0; padding:0; box-sizing:border-box; }}

  body {{
    font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    min-height: 100vh;
    display: flex;
    color: #374151;
    background: #f9fafb;
  }}

  .brand-panel {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 48px 64px;
    background: linear-gradient(160deg, #0f172a 0%, #1e3a5f 50%, #0f172a 100%);
    position: relative;
    overflow: hidden;
  }}
  .brand-panel .glow {{
    position: absolute;
    top: 20%; left: -10%;
    width: 60%; height: 60%;
    background: radial-gradient(ellipse, rgba(5,112,222,.15) 0%, transparent 70%);
    filter: blur(60px);
    pointer-events: none;
  }}
  .brand-panel .inner {{
    position: relative; z-index: 1; max-width: 520px;
  }}
  .brand-panel .logo {{ margin-bottom: 40px; }}
  .brand-panel h1 {{
    font-size: 36px; line-height: 1.2; font-weight: 600;
    color: #fff; margin-bottom: 12px; letter-spacing: -0.02em;
  }}
  .brand-panel p {{
    font-size: 15px; line-height: 1.6;
    color: rgba(255,255,255,.6); max-width: 440px;
  }}

  .auth-panel {{
    width: 520px; flex-shrink: 0;
    display: flex; flex-direction: column;
    justify-content: center; align-items: center;
    padding: 48px;
    background: #fff;
    border-left: 1px solid #f3f4f6;
  }}
  .auth-panel .mobile-logo {{ display: none; margin-bottom: 24px; }}
  .auth-inner {{ width: 100%; max-width: 420px; }}

  .auth-inner > h2 {{
    font-size: 18px; font-weight: 600;
    color: #374151; margin-bottom: 4px;
  }}
  .auth-inner > .sub {{
    font-size: 13px; color: #6b7280; margin-bottom: 24px;
  }}

  .info-box {{
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 6px;
    padding: 14px 16px;
    font-size: 14px;
    line-height: 1.5;
    color: #1e40af;
    margin-bottom: 20px;
  }}
  .info-box strong {{ font-weight: 600; }}

  .redirect-box {{
    background: #fefce8;
    border: 1px solid #fde68a;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 20px;
  }}
  .redirect-box .label {{
    font-size: 12px; font-weight: 500;
    color: #92400e; display: block; margin-bottom: 4px;
  }}
  .redirect-box .value {{
    font-size: 13px; font-family: 'SF Mono', Menlo, monospace;
    color: #78350f; word-break: break-all;
  }}

  details {{
    margin-bottom: 24px;
  }}
  summary {{
    font-size: 13px; font-weight: 500;
    color: #6b7280; cursor: pointer;
    padding: 8px 0;
  }}
  summary:hover {{ color: #374151; }}
  .detail-grid {{
    margin-top: 8px;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    overflow: hidden;
  }}
  .detail-row {{
    display: flex;
    border-bottom: 1px solid #f3f4f6;
    font-size: 13px;
  }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-label {{
    width: 140px; flex-shrink: 0;
    padding: 8px 12px;
    background: #f9fafb;
    color: #6b7280;
    font-weight: 500;
  }}
  .detail-value {{
    flex: 1;
    padding: 8px 12px;
    color: #374151;
    word-break: break-all;
  }}

  .btn-group {{
    display: flex; gap: 12px;
  }}
  .btn {{
    flex: 1;
    height: 44px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    border: none;
    transition: all 150ms ease;
  }}
  .btn-approve {{
    background: #0570de; color: #fff;
  }}
  .btn-approve:hover {{
    background: #0559b3;
    box-shadow: 0 1px 2px rgba(0,0,0,.04);
  }}
  .btn-deny {{
    background: #f3f4f6; color: #374151;
    border: 1px solid #e5e7eb;
  }}
  .btn-deny:hover {{
    background: #e5e7eb;
  }}

  @media (max-width: 768px) {{
    body {{ flex-direction: column; }}
    .brand-panel {{ display: none; }}
    .auth-panel {{
      width: 100%; border-left: none;
      padding: 32px 24px;
    }}
    .auth-panel .mobile-logo {{ display: block; }}
  }}
</style>
</head>
<body>

<div class="brand-panel">
  <div class="glow"></div>
  <div class="inner">
    <div class="logo">{logo_56}</div>
    <h1>Authorize<br>application access.</h1>
    <p>
      Review the details below and confirm you trust this application
      before granting it access to the RunWhen platform.
    </p>
  </div>
</div>

<div class="auth-panel">
  <div class="mobile-logo">{logo_48}</div>
  <div class="auth-inner">
    <h2>Application Access Request</h2>
    <p class="sub">Confirm you recognize this application.</p>

    <div class="info-box">
      The application <strong>{client_display}</strong> wants to access
      the MCP server <strong>{server_display}</strong>.
    </div>

    <div class="redirect-box">
      <span class="label">Credentials will be sent to:</span>
      <div class="value">{redirect_escaped}</div>
    </div>

    <details>
      <summary>Advanced Details</summary>
      <div class="detail-grid">
        <div class="detail-row">
          <div class="detail-label">Application</div>
          <div class="detail-value">{client_display}</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Application ID</div>
          <div class="detail-value">{html_module.escape(client_id)}</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Redirect URI</div>
          <div class="detail-value">{redirect_escaped}</div>
        </div>
        <div class="detail-row">
          <div class="detail-label">Scopes</div>
          <div class="detail-value">{scopes_display}</div>
        </div>
      </div>
    </details>

    <form id="consentForm" method="POST" action="">
      <input type="hidden" name="txn_id" value="{txn_id}" />
      <input type="hidden" name="csrf_token" value="{csrf_token}" />
      <input type="hidden" name="submit" value="true" />
      <div class="btn-group">
        <button type="submit" name="action" value="approve"
                class="btn btn-approve">Allow Access</button>
        <button type="submit" name="action" value="deny"
                class="btn btn-deny">Deny</button>
      </div>
    </form>
  </div>
</div>

</body>
</html>"""


def patch_fastmcp_consent_ui() -> None:
    """Monkey-patch FastMCP's consent page with the RunWhen-styled version."""
    import fastmcp.server.auth.oauth_proxy.ui as proxy_ui

    proxy_ui.create_consent_html = create_consent_html
