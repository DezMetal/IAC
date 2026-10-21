# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
The optional web GUI for a Cell App -- DSS, served by the app itself.

A Cell App may declare a `ui` block in its manifest. If it does, it gets a
browser interface that looks like every other D-Net Lab property, without
writing any CSS and without the launcher rendering anything.

HOW IT DIVIDES

* **The app** owns its own HTTP surface and its own fragment of markup. It
  already serves JSON; the GUI is the same data with a face.
* **This module** serves the shell: `<head>`, DSS, the theme controller, a
  header, and the mount point. One file, so every Cell App's GUI is
  consistent by construction rather than by everyone remembering.
* **The launcher** serves nothing. `apps.open` hands back a URL. A launcher
  that rendered app UIs would become the thing every app had to be written
  for, which is the opposite of the point.

ON DSS

`DSS/README.md` is explicit: *"This directory is the single source of truth.
Do not edit a copy inside a consuming project -- edit here, then run
`python sync.py`."*

So nothing here contains CSS. `stylesheet()` finds the canonical `DSS/dss.css`
when the lab tree is present, and falls back to a synced copy beside this file
for an app deployed somewhere the lab tree is not. It reports which it used,
because a GUI silently running on a stale copy is the failure that rule
exists to prevent.

`IAC/apps/ui/` is registered in `DSS/targets.json`, so `python sync.py` keeps
that fallback current.
"""

import json
import os

__all__ = ["shell", "stylesheet", "theme_script", "serve_ui_request",
           "UI_ROUTE_PREFIX"]

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCAL_DSS = os.path.join(_HERE, "ui")

#: Routes this module answers, so an app can hand them straight over.
UI_ROUTE_PREFIX = "/ui"


def _lab_dss():
    """The canonical DSS directory, if the lab tree is here."""
    # _HERE is <lab>/IAC/apps, so the lab root is two levels up, not three.
    lab = os.path.dirname(os.path.dirname(_HERE))
    candidate = os.path.join(lab, "DSS")
    return candidate if os.path.isfile(os.path.join(candidate, "dss.css")) \
        else None


def _asset(name):
    """(text, source) for a DSS asset. Canonical first, synced copy second."""
    lab = _lab_dss()
    for directory, source in ((lab, "canonical"), (_LOCAL_DSS, "synced copy")):
        if not directory:
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return handle.read(), source
            except OSError:
                continue
    return None, None


def stylesheet():
    """(css, source). `source` names which copy, or None if there is none."""
    return _asset("dss.css")


def theme_script():
    """(js, source). DSS's optional theme controller."""
    return _asset("dss.js")


def _nav(links):
    """The nav row. `.dss-button--secondary` because these are not the
    page's primary action."""
    if not links:
        return ""
    items = "".join(
        '<a class="dss-button dss-button--secondary dss-button--sm" '
        'href="%s">%s</a>' % (href, label) for label, href in links)
    return '<nav class="cellapp-nav">%s</nav>' % items


def shell(app, body="", links=(), subtitle="", scripts=(), eyebrow="Cell App",
          no_js=""):
    """The page every Cell App GUI is wrapped in.

    `body` is the app's own markup. Everything around it comes from here, so a
    GUI written by one app looks like a GUI written by any other.

    DNetLab/WEB_STANDARDS.md governs visitor-facing properties rather than app
    interfaces, but several of its rules are about not shipping something that
    looks broken, and those apply to anything with a face:

    * **The redundancy budget (section 3).** The tagline gets the nav lockup,
      which is free, plus at most one body appearance *where its meaning is
      explained*. An app GUI explains nothing, so it gets the lockup and
      nothing else. The first draft put "From Mind To Matter" in the footer as
      decoration, which is precisely the motto-devalued-by-repetition the rule
      was written for.
    * **Render without JS (section 7b).** "Never let content be trapped
      invisible." An app GUI is a client for an API, so it genuinely needs
      JS to show live data -- but it must say so rather than sitting on the
      word "loading" forever. Hence `<noscript>`, and static markup that
      states what it is before any script runs.
    * **Every block earns a surface (section 7a).** No small bare text in a
      wide space. Secondary detail goes in a `.dss-bar` or behind a
      `.dss-rule-iri` keyline, not in shrunken grey type.

    Only real DSS classes are used. Four invented ones in the first draft --
    `dss-btn`, `dss-check`, `dss-callout`, `dss-muted` -- do not exist in
    `DSS/dss.css`, and a GUI referencing them renders unstyled while looking
    finished in the source.
    """
    css, css_source = stylesheet()
    style_tag = ('<link rel="stylesheet" href="%s/dss.css">' % UI_ROUTE_PREFIX
                 if css else "")
    warning = "" if css else (
        '<div class="dss-card dss-card--flat" role="alert">'
        '<strong>DSS was not found, so this page is unstyled.</strong> '
        'It lives at DSS/dss.css in the lab tree. For a deployed app, run '
        'python sync.py in that directory to refresh the copy in '
        'IAC/apps/ui/.</div>')

    script_tags = "".join('<script src="%s" defer></script>' % src
                          for src in scripts)
    theme, _ = theme_script()
    theme_tag = ('<script src="%s/dss.js" defer></script>' % UI_ROUTE_PREFIX
                 if theme else "")

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>%(title)s</title>
%(style)s
<style>
  .cellapp-head { display:flex; align-items:flex-end; gap:1.25rem;
                  flex-wrap:wrap; margin:2.25rem 0 1.75rem; }
  .cellapp-nav  { display:flex; gap:.5rem; flex-wrap:wrap; margin-left:auto; }
  .cellapp-foot { margin:3rem 0 2.5rem; padding:1.15rem 0 0;
                  font-family:var(--dss-font-mono); font-size:.78rem;
                  letter-spacing:.04em; color:var(--dss-ink-soft); }
  .cellapp-detail { margin-top:1rem; padding:.9rem 1.15rem;
                    font-family:var(--dss-font-mono); font-size:.82rem;
                    color:var(--dss-ink-soft); }
</style>
</head>
<body>
<div class="dss-container">
  <header class="cellapp-head">
    <div>
      <p class="dss-eyebrow" style="margin:0 0 .35rem">%(eyebrow)s</p>
      <h1 class="dss-display" style="margin:0">%(title)s</h1>
      %(subtitle)s
    </div>
    %(nav)s
  </header>
  <span class="dss-rule-iri" aria-hidden="true"></span>

  <noscript>
    <div class="dss-card dss-card--flat" style="margin-top:1.75rem">
      <h2 style="margin-top:0">This interface needs JavaScript</h2>
      <p>It reads live data from the app over its own JSON API, so there is
      nothing to show without it. %(no_js)s</p>
    </div>
  </noscript>

  %(warning)s
  <main id="cellapp-body" style="margin-top:1.75rem">%(body)s</main>

  <footer class="cellapp-foot dss-bar dss-bar--bottom"
          style="padding-left:1.15rem; padding-right:1.15rem">
    %(title)s &middot; a Cell App &middot;
    <a href="https://lab.dnet.live">D-Net Lab</a>%(dss_note)s
  </footer>
</div>
%(theme)s
%(scripts)s
</body>
</html>
""" % {
        "title": app,
        "eyebrow": eyebrow,
        "subtitle": ('<p style="margin:.5rem 0 0; max-width:60ch; '
                     'color:var(--dss-ink-soft)">%s</p>' % subtitle)
                    if subtitle else "",
        "style": style_tag,
        "nav": _nav(links),
        "warning": warning,
        "body": body,
        "no_js": no_js or ("Every one of its operations is also available from "
                           "a terminal and over IAC, which need no browser at "
                           "all."),
        "theme": theme_tag,
        "scripts": script_tags,
        "dss_note": (" &middot; DSS (%s)" % css_source) if css_source else "",
    }


def serve_ui_request(path):
    """(status, content_type, body) for a `/ui/...` route, or None.

    An app's HTTP handler delegates here rather than learning where DSS lives:

        handled = serve_ui_request(self.path)
        if handled:
            status, content_type, body = handled
            ...
    """
    route = (path or "").split("?", 1)[0].rstrip("/")
    if route == UI_ROUTE_PREFIX + "/dss.css":
        css, _ = stylesheet()
        if css is None:
            return 404, "text/plain", b"DSS not found"
        return 200, "text/css; charset=utf-8", css.encode("utf-8")
    if route == UI_ROUTE_PREFIX + "/dss.js":
        js, _ = theme_script()
        if js is None:
            return 404, "text/plain", b"dss.js not found"
        return 200, "application/javascript; charset=utf-8", js.encode("utf-8")
    return None


def describe():
    """What the GUI layer found. For `apps.list` and a doctor."""
    css, css_source = stylesheet()
    js, js_source = theme_script()
    return {
        "dss_available": css is not None,
        "dss_source": css_source,
        "dss_bytes": len(css) if css else 0,
        "theme_controller": js is not None,
        "theme_source": js_source,
        "canonical_dss": _lab_dss(),
        "note": ("DSS is the single source of truth at DSS/dss.css. A synced "
                 "copy in IAC/apps/ui/ is for deployments without the lab "
                 "tree; refresh it with `python sync.py` in DSS/."),
    }


if __name__ == "__main__":
    print(json.dumps(describe(), indent=2))
