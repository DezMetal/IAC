# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import json, os, re, sys, time, argparse, subprocess, requests, base64, threading
import urllib.parse
from urllib.parse import quote_plus
from pathlib import Path
from playwright.sync_api import sync_playwright

# Import core brain logic
try:
    from ..agent_tools import task_ai_process, task_encode, resolve_resource, get_skip_duplicates_option, _seen_hashes, get_duplicate_threshold_option
    from ..inference import DEFAULT_AI_CONFIG
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from agent_tools import task_ai_process, task_encode, resolve_resource, get_skip_duplicates_option, _seen_hashes, get_duplicate_threshold_option
    from inference import DEFAULT_AI_CONFIG

# Words too common to prove a result is on topic. Deliberately short: the
# check asks whether ANY distinctive word survived, so over-trimming here
# would start calling good searches bad.
_QUERY_STOPWORDS = frozenset("""
the a an and or of for to in on at is are was were be been how what why when
where which who site www com net org official website page find search show
tell me my your our their this that with from about into over under best top
new latest guide tutorial docs documentation info information
""".split())

class WebAgent:
    def __init__(self, master_input):
        if isinstance(master_input, str) and os.path.isfile(master_input):
            with open(master_input, 'r', encoding="utf-8") as f: self.master = json.load(f)
        else: self.master = master_input

        self.web_cfg = self.master.get("config", self.master.get("web_agent", self.master))
        self.ai_cfg = self.web_cfg.get("ai_config", self.web_cfg.get("ai_core", self.web_cfg))
        self.output_path = self.web_cfg.get("output", "session_results.json")
        
        self.workflow = resolve_resource(self.master.get("workflow", self.master.get("plan", [])), is_list=True)
        self.payload = resolve_resource(self.master.get("data_payload", {}), is_list=False)

        # The sync Playwright API may only be driven from the thread that
        # started it; anything else needs to know that before it tries.
        self._owner_thread = threading.get_ident()
        self._state_saved = None

        self.pw = sync_playwright().start()
        
        # Context Configuration
        ctx_opts = {}
        launch_args = [
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--disable-features=PaintHolding",
            "--disable-checker-imaging",
        ]

        # 2. Handle Video Recording & Quality
        v_size = self.web_cfg.get("record_video_size", {"width": 1920, "height": 1080})
        
        # If UI is enabled, optimize for full screen experience
        if not self.web_cfg.get("headless", True):
            ctx_opts["viewport"] = None
            ctx_opts["no_viewport"] = True
            launch_args.append("--start-maximized")
        else:
            ctx_opts["viewport"] = v_size # Synchronized with target recording size
        
        # 1. Handle Storage State (Snapshot-based cookies/storage)
        #
        # A malformed state file must not be fatal. Playwright raises when
        # handed one, which takes down the browser launch -- including the
        # sandbox launch you need in order to log in and produce a good file.
        # A bad session is a reason to start clean and say so, never a reason
        # to be unable to start at all.
        # A signed-in session by default, if one has been saved.
        #
        # Search engines serve a bot challenge to a cold browser -- the HTML
        # endpoint answered a query with "select all squares containing a
        # duck". Nothing here should be in the business of defeating that.
        # Carrying the cookies from a real sign-in is the supported way to be
        # a recognised client, and it is the difference between search
        # working and search silently returning nothing.
        if not self.web_cfg.get("storage_state"):
            # IAC/auth/session.json is THE location. One canonical path, and
            # `web.save_state` writes there, so a saved sign-in is picked up
            # without anyone having to wire it. A second copy elsewhere only
            # creates the chance of a stale one shadowing the real thing.
            default_state = os.path.join(os.path.dirname(__file__), "..",
                                         "auth", "session.json")
            if os.path.exists(default_state):
                self.web_cfg["storage_state"] = os.path.abspath(default_state)

        if self.web_cfg.get("storage_state"):
            state_path = os.path.abspath(self.web_cfg["storage_state"])
            if os.path.exists(state_path):
                problem = None
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    if not isinstance(state, dict):
                        problem = "not a JSON object"
                    elif "cookies" not in state and "origins" not in state:
                        problem = "no 'cookies' or 'origins' key -- not a Playwright storage state"
                except json.JSONDecodeError as e:
                    problem = f"not valid JSON ({e})"
                except OSError as e:
                    problem = f"unreadable ({e})"

                if problem:
                    self._log("WARN", f"Ignoring storage state {state_path}: {problem}. "
                                      f"Starting with a clean session -- log in and save "
                                      f"a new one with web.save_state.")
                else:
                    n = len(state.get("cookies") or [])
                    ctx_opts["storage_state"] = state_path
                    self._log("SYSTEM", f"Loading storage state: {state_path} ({n} cookies)")
        
        if self.web_cfg.get("record_video"):
            v_dir = self.web_cfg.get("record_video_dir", "./recordings/")
            os.makedirs(v_dir, exist_ok=True)
            ctx_opts["record_video_dir"] = v_dir
            ctx_opts["record_video_size"] = v_size
            self._log("SYSTEM", f"Recording enabled: {v_dir} ({v_size['width']}x{v_size['height']})")

        # 3. Launch Browser (Persistent vs Transient)
        user_data_dir = self.web_cfg.get("user_data_dir")
        if user_data_dir:
            # Persistent mode: profile is a directory on disk
            user_data_dir = os.path.abspath(user_data_dir)
            self._log("SYSTEM", f"Persistent Mode enabled: {user_data_dir}")
            self.browser = None # Browser is managed by persistent context
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir,
                headless=self.web_cfg.get("headless", True),
                args=launch_args,
                **ctx_opts
            )
        else:
            # Transient mode: clean state every time (default)
            self.browser = self.pw.chromium.launch(
                headless=self.web_cfg.get("headless", True),
                args=launch_args
            )
            self.context = self.browser.new_context(**ctx_opts)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()


        self.closed = False
        self.registry = {
            "goto": self._cmd_goto,
            "probe": self._cmd_probe,
            "analyze": self._cmd_analyze,
            "snap": self._cmd_snap,
            "extract": self._cmd_extract,
            "search": self._cmd_search,
            "push": self._cmd_push,
            "eval": self._cmd_eval,
            "brain": self._cmd_brain,
            "click": self._cmd_click,
            "type": self._cmd_type,
            "wait": lambda a: self._wrap("WAIT", a, lambda: self.page.wait_for_timeout(a.get("ms", 1000))),
            "scroll": self._cmd_scroll,
            "tour": self._cmd_tour,
            "stop": self._cmd_stop,
            "fs_read": self._cmd_fs_read,
            "fs_write": self._cmd_fs_write,
            "exec": self._cmd_exec,
            "plan": self._cmd_plan,
            "save_state": self._cmd_save_state,
            "modify": self._cmd_modify,
            "sandbox": self._cmd_sandbox,
            # Modern filesystem vocabulary. The sandbox dispatches on the last
            # dotted segment, so these keys make `filesystem.write` work here
            # AND make an exported chain resolvable by the runner -- fs_read /
            # fs_write are no longer registered operations, so a chain built
            # with them used to die on replay with "Unregistered operation".
            "read": self._cmd_fs_read,
            "write": self._cmd_fs_write,
            "copy": self._cmd_fs_copy,
            # Core payload ops, delegated to the runner's implementations so
            # the sandbox and a replayed plan behave identically.
            "set": lambda a: self._core("set", a),
            "append": lambda a: self._core("append", a),
            "parse_json": lambda a: self._core("parse_json", a),
            "log": lambda a: self._core("log", a),
        }

    def _core(self, op_name, args):
        """Run a runner core operation against this session's payload."""
        try:
            from runner import _fallback_execute
        except ImportError:
            from ..runner import _fallback_execute
        return _fallback_execute(op_name, args, self.payload, self.ai_cfg)

    def _cmd_fs_copy(self, args):
        s = time.time()
        src = args.get("src") or args.get("from")
        dst = args.get("dst") or args.get("to")
        if not src or not dst:
            return {"status": "error", "error": "copy requires 'src' and 'dst'"}
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        if not os.path.isfile(src):
            return {"status": "error", "error": f"Source file not found: {src}"}
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(data)
        self._log("FS_COPY", f"{os.path.basename(src)} -> {dst} ({len(data)} bytes)", s)
        return {"status": "ok", "src": src, "dst": dst, "size": len(data)}
    
    def _cmd_stop(self, args):
        """Explicitly stop the session, saving any active recordings."""
        self._log("SYSTEM", "Stop requested. Finalizing session...")
        self.close()
        return {"status": "stopped"}

    def close(self):
        if self.closed: return
        self.closed = True

        # Honour save_storage_state here rather than at one call site, because
        # this is the only point every entry path goes through. It used to be
        # handled solely in webagent.py's __main__ block, so a sandbox login
        # driven through iac.py -- the normal way to run a pipeline -- threw
        # the session away on exit and left the config line looking like it
        # had done something. Must run BEFORE the context closes.
        target = self.web_cfg.get("save_storage_state")
        already = getattr(self, "_state_saved", None)
        if target and not already and self._context_alive():
            try:
                self._cmd_save_state({"path": target})
            except Exception as e:
                # Teardown must never raise. The session is either on disk or
                # it is not; either way the process still has a recording to
                # finalise and an exit code to report.
                self._log("WARN", f"Could not save storage state on close: {e}")
        elif already:
            self._log("SYSTEM", f"Storage state already saved to {already}")

        try:
            if hasattr(self, "page") and self.page:
                self.page.close()
        except: pass
        try:
            if hasattr(self, "context") and self.context:
                self.context.close()
        except: pass
        try:
            if hasattr(self, "browser") and self.browser:
                self.browser.close()
        except: pass
        try:
            if hasattr(self, "pw") and self.pw:
                self.pw.stop()
        except: pass

    def _log(self, cat, msg, start=None):
        dur = f" ({time.time()-start:.2f}s)" if start else ""
        print(f"[{cat:^10}] {msg}{dur}")

    # How much page text one read returns. Roughly 1,000 tokens -- enough
    # that an article's substance arrives in the first call, small enough that
    # a single page cannot evict the persona and the operations index from the
    # prompt. `offset` pages through the rest.
    PAGE_TEXT_BUDGET = 4000

    def _cmd_goto(self, args):
        s = time.time()
        url = args.get("url") or args.get("query") or args.get("input") or args.get("search") or ""
        url = str(url).strip()
        if not url:
            return {"status": "error", "error": "No URL or search query provided to web.goto"}

        import os
        import urllib.parse
        
        # 1. Already has a valid protocol
        if url.startswith("http://") or url.startswith("https://") or url.startswith("file://"):
            pass
        # 2. Explicitly local path (starts with ., /, \, or Windows drive letter)
        elif url.startswith(".") or url.startswith("/") or url.startswith("\\") or (len(url) > 1 and url[1] == ":"):
            abs_path = os.path.abspath(url)
            url = "file:///" + abs_path.replace("\\", "/")
        # 3. Looks like a domain (e.g. example.com or www.example.com)
        elif "." in url.split("/")[0] or url.startswith("www."):
            url = "https://" + url
        # 4. Fallback: treat plain text queries as web search queries
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote(url)}"
            
        wait = args.get("wait_until", "domcontentloaded")
        timeout = args.get("timeout", 60000)
        
        try:
            res = self.page.goto(url, wait_until=wait, timeout=timeout)
            self._log("NETWORK", f"URL: {url} ({wait})", s)
            # Come back with something to SAY, not just a status code.
            #
            # `goto` returned {status, url} and nothing else, so "go to X and
            # check it out" ended with the agent arriving and reporting that
            # it could not see any content -- which was true, and which is a
            # silly place for the system to leave it. The page was fully
            # loaded; nobody had read it. A title and the opening text make a
            # single navigation answerable, and web.extract is still there
            # when more than the opening is wanted.
            out = {"status": res.status if res else 200, "url": self.page.url}
            try:
                out["title"] = self.page.title()
                text = (self.page.inner_text("body") or "").strip()
                if text:
                    # THE PAGE, not a glimpse of it.
                    #
                    # This returned `preview: text[:800]` and called it done.
                    # Eight hundred characters of a fifty-thousand character
                    # page is the masthead and a cookie notice -- so "open
                    # their site and tell me the motto" arrived, loaded
                    # correctly, and handed back the top of the header. She
                    # cannot research from that, and the failure looks like
                    # her not reading rather than the page never having been
                    # given to her.
                    #
                    # Windowed exactly like filesystem.read, because it is the
                    # same problem and she already knows that shape: a real
                    # budget, and when there is more, the offset that
                    # continues it. Paging costs a round trip; being handed
                    # the wrong 800 characters costs the whole task.
                    budget = int(args.get("limit") or self.PAGE_TEXT_BUDGET)
                    start = max(0, int(args.get("offset") or 0))
                    window = text[start:start + budget]
                    out["chars"] = len(text)
                    out["offset"] = start
                    out["text"] = window
                    # `preview` kept: callers and prompts still name it, and a
                    # key that quietly vanishes is its own outage.
                    out["preview"] = window
                    if start + budget < len(text):
                        out["next_offset"] = start + budget
                        out["more"] = (
                            "Showing characters %d-%d of %d. To continue, call "
                            "web.goto again with this same url and offset=%d. "
                            "To find something specific instead of reading on, "
                            "use web.extract with a selector."
                            % (start, start + len(window), len(text),
                               start + budget))
                    elif start:
                        out["more"] = ("Characters %d-%d of %d -- this is the "
                                       "end of the page."
                                       % (start, start + len(window), len(text)))
            except Exception:
                pass
            return out
            
        except Exception as e:
            err_str = str(e)
            self._log("WARN", f"Goto timed out or failed: {err_str[:100]}")
            # Tolerate timeouts for live streaming pages that never finish loading
            if "Timeout" in err_str or "timeout" in err_str.lower():
                return {"status": 200, "url": self.page.url, "warning": "Page load timed out (likely a live stream)"}
            return {"status": "error", "url": self.page.url, "error": err_str}

    @staticmethod
    def _target_of(args):
        """The element an action is aimed at, under whichever key it arrived.

        `selector` is the documented name; `target`, `ref` and `element` are
        what other tool vocabularies call the same thing, and a call that
        names the right element under the wrong key should act, not fail.
        """
        for key in ("selector", "target", "ref", "element"):
            if args.get(key):
                return args[key]
        return None

    def _cmd_click(self, args):
        s = time.time()
        selector = self._target_of(args)
        if not selector:
            return {"status": "error", "error": "web.click needs a selector"}

        js_check = "(s) => window.IAC_PAYLOAD && window.IAC_PAYLOAD[s] instanceof Node"
        if self.page.evaluate(js_check, selector):
            self.page.evaluate("(s) => window.IAC_PAYLOAD[s].click()", selector)
        else:
            self.page.click(selector)
        self._log("CLICK", str(selector), s)
        return {"status": "ok"}

    def _cmd_type(self, args):
        s = time.time()
        selector = self._target_of(args)
        if not selector:
            return {"status": "error", "error": "web.type needs a selector"}
        val = args.get("value")
        if val is None:
            val = args.get("text", args.get("val", ""))
        val = "" if val is None else str(val)
        # fill() replaces the field's contents; clear=false appends keystrokes
        # to whatever is already there.
        clear_first = args.get("clear", True)

        js_check = "(s) => window.IAC_PAYLOAD && window.IAC_PAYLOAD[s] instanceof Node"
        if self.page.evaluate(js_check, selector):
            self.page.evaluate("(args) => window.IAC_PAYLOAD[args.s].value = args.val", {"s": selector, "val": val})
        elif clear_first:
            self.page.fill(selector, val)
        else:
            self.page.type(selector, val)
        self._log("TYPE", f"{selector} -> {val}", s)
        return {"status": "ok"}

    def _cmd_probe(self, args):
        s = time.time()
        # Restored discovery logic
        elements = self.page.evaluate("""() => 
            Array.from(document.querySelectorAll('button, a, input, [role="button"]'))
            .filter(el => el.offsetParent !== null)
            .map(el => ({
                text: el.innerText.trim() || el.placeholder || el.value,
                selector: `text="${el.innerText.trim()}"`
            })).slice(0, 30)
        """)
        self._log("PROBE", f"Found {len(elements)} items", s)
        return {"status": "ok", "message": f"Found {len(elements)} items", "elements": elements}

    def _cmd_snap(self, args):
        s = time.time()
        path = args.get("path") or f"snap_{int(time.time())}.png"
        full = str(args.get("full_page", "true")).lower() == "true"
        
        self.page.screenshot(path=path, full_page=full)
        key = args.get("payload_key") or f"image_{len(self.payload)}"
        self.payload[key] = {"src": path}
        self._log("VISION", f"Snapshot saved to {key} (Full: {full})", s)
        return {"status": "ok", "key": key, "path": path}

    # Result blocks, read STRUCTURALLY: a heading inside a link, with the text
    # around it. Engine markup is obfuscated and renamed without notice, but
    # "the headline is a link" has held for as long as search has existed, so
    # this works across engines and keeps working when classes churn.
    _SEARCH_JS = """(arg) => {
        const txt = (el) => ((el && (el.innerText || el.textContent)) || '')
                              .replace(/\s+/g, ' ').trim();
        // THE REAL DESTINATION, not the tracker.
        //
        // Bing wraps every result in /ck/a?...&u=<base64>, so a search handed
        // back eight `bing.com/ck/a?!&&p=45d13a0a...` links. Useless twice
        // over: the caller cannot tell one result from another by looking,
        // and passing one to web.goto fetches a redirect page. The true URL
        // is in the `u` parameter, base64url with a two-character prefix.
        const real = (href) => {
            try {
                const u = new URL(href);
                if (!/(^|\.)bing\.com$/.test(u.hostname)) return href;
                let p = u.searchParams.get('u');
                if (!p) return href;
                if (/^a\d/.test(p)) p = p.slice(2);
                p = p.replace(/-/g, '+').replace(/_/g, '/');
                while (p.length % 4) p += '=';
                const decoded = atob(p);
                return /^https?:/i.test(decoded) ? decoded : href;
            } catch (e) { return href; }
        };
        const out = [], seen = new Set();
        for (const el of document.querySelectorAll('h2 a, h3 a, a h2, a h3')) {
            if (out.length >= arg.limit) break;
            const link = el.tagName === 'A' ? el : el.closest('a');
            if (!link || !link.href || !/^https?:/.test(link.href)) continue;
            const href = real(link.href);
            if (seen.has(href)) continue;
            const title = txt(el) || txt(link);
            if (!title) continue;
            seen.add(href);
            let blk = link.closest('li,article,div');
            for (let i = 0; i < 3 && blk && txt(blk).length < title.length + 60; i++) {
                blk = blk.parentElement;
            }
            const snip = txt(blk).replace(title, '').trim();
            out.push({title: title.slice(0, 140), url: href,
                      snippet: snip.slice(0, 240)});
        }
        return out;
    }"""

    #: Sent on the plain-HTTP search. Without one, urllib announces itself as
    #: Python and Chromium announces itself as headless -- and an engine that
    #: knows it is talking to a robot answers like it.
    _DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36")

    def _search_via_feed(self, query, limit):
        """Bing's own RSS for the same search. Returns rows, or None.

        None means "could not", never "found nothing" -- an empty result set
        is an answer and must not silently fall through to the scraper and be
        asked twice.
        """
        url = ("https://www.bing.com/search?q=%s&format=rss&count=%d"
               % (quote_plus(query), max(1, min(int(limit or 8), 20))))
        try:
            reply = requests.get(
                url, timeout=12,
                headers={"User-Agent": self._DESKTOP_UA,
                         "Accept-Language": "en-US,en;q=0.9"})
            if reply.status_code != 200 or "<item>" not in reply.text:
                return None
            body = reply.text
        except Exception as e:
            self._log("SEARCH", "RSS unavailable (%s); using the page"
                                % str(e).splitlines()[0][:60])
            return None

        def unescape(raw):
            import html as _html
            return _html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()

        rows = []
        for item in re.findall(r"<item>(.*?)</item>", body, re.S):
            title = re.search(r"<title>(.*?)</title>", item, re.S)
            link = re.search(r"<link>(.*?)</link>", item, re.S)
            desc = re.search(r"<description>(.*?)</description>", item, re.S)
            href = unescape(link.group(1)) if link else ""
            if not href.startswith("http"):
                continue
            rows.append({"title": unescape(title.group(1))[:140] if title else "",
                         "url": href,
                         "snippet": unescape(desc.group(1))[:400] if desc else ""})
            if len(rows) >= limit:
                break
        return rows or None

    def _searched(self, key, query, results, engine):
        """One shape for a completed search, whichever path produced it."""
        note = None
        if results:
            terms = [w for w in re.findall(r"[A-Za-z0-9][\w.\-]{2,}", query.lower())
                     if w not in _QUERY_STOPWORDS]
            if terms:
                blob = " ".join(
                    "%s %s %s" % (r.get("title", ""), r.get("url", ""),
                                  r.get("snippet", "")) for r in results).lower()
                hits = [t for t in terms if t in blob]
                # A MINORITY is still a failed search.
                #
                # This asked whether ANY distinctive word survived, which a
                # degraded engine passes trivially: 'ollama num_ctx kv cache
                # vram' returns the Ollama homepage and its download page,
                # 'ollama' appears, and the check stays quiet about results
                # that answered none of the actual question. Bing degrades
                # a client that has queried it steadily -- measured here on
                # queries never sent before, python.org for an asyncio
                # question, sqlite.org for a WAL question -- and the shape
                # it degrades INTO is always the first noun's home page.
                # One word out of five matching is the signature of that,
                # not of a search worth reading.
                if len(hits) * 2 < len(terms):
                    note = (
                        "Only %d of the %d distinctive words in this query "
                        "(%s) appear anywhere in these %d results, so %s almost "
                        "certainly answered a broader query than the one "
                        "asked. Treat these as unrelated. Searching again with "
                        "similar words will return the same thing -- go "
                        "straight to a known URL with web.goto if you have "
                        "one, or say the search is not finding it."
                        % (len(hits), len(terms), ", ".join(terms[:6]),
                           len(results), engine))
        out = {"status": "ok", "key": key, "count": len(results),
               "query": query, "results": results}
        if note:
            out["relevance"] = "none"
            out["message"] = note
        return out

    def _cmd_search(self, args):
        """Search the web and return titles, links and snippets.

        Exists because an agent asked to "look up X" reaches for a search
        operation and there was not one -- it analysed the blank browser twice
        at twelve seconds each, then emitted `web.search` anyway and had the
        call dropped as unregistered. The plan was right every time.

        Defaults to Bing. Google and Startpage answer an automated client with
        a human-verification page for this network; Bing answers with results.
        Defeating a challenge is not something this does, and picking an engine
        that will simply talk to us is the honest way through. `search_url` in
        the web config overrides it -- `{q}` is the encoded query -- so pointing
        at Google, or at a commercial search API, is a config change.
        """
        s = time.time()
        query = (args.get("query") or args.get("q") or "").strip()
        if not query:
            return {"status": "error", "error": "search needs a query"}
        limit = int(args.get("limit", 8) or 8)
        key = args.get("payload_key", "search_results")

        template = (args.get("search_url") or self.web_cfg.get("search_url")
                    or "https://www.bing.com/search?q={q}")
        url = template.replace("{q}", quote_plus(query))
        engine = urllib.parse.urlparse(url).netloc.replace("www.", "")

        # THE FEED FIRST, THE BROWSER ONLY IF IT FAILS.
        #
        # Scraping the search PAGE gets the page a bot is served: measured
        # here, "ollama num_ctx kv cache vram" came back as the Ollama
        # homepage and its download link, and "playwright sync api cannot
        # switch to a different thread" as playwright.dev and the repo. One
        # query word in six survived. Not a parsing fault -- those were the
        # real organic results, and plain curl with a desktop user agent got
        # the same, so it is what the HTML endpoint serves, not something a
        # better selector or a fresh session could fix.
        #
        # Bing publishes the same search as RSS. Same engine, no key, nobody
        # else in the loop, and it answers the actual question: the same two
        # queries return 6/6 and 3/3 of their words, the exact GitHub issue
        # and Stack Overflow thread for the first, real tuning articles for
        # the second -- each with a description worth reading rather than a
        # bare title.
        #
        # It also needs no browser at all, so a search no longer waits on
        # Playwright, cannot be broken by the browser being wedged, and costs
        # a fraction of the time. The page scrape stays as the fallback, and
        # is still the path when someone points search_url somewhere else.
        # Gated on WHICH ENGINE, not on whether anyone wrote the setting
        # down. This first read `if not search_url`, treating any configured
        # value as "the operator wants the page scraped" -- and config.json
        # here already contained the default Bing URL, written out in full.
        # So the feed was skipped on the one machine it was built for, and
        # the fix would have looked like it did nothing at all.
        #
        # RSS is a Bing feature, so the real question is only ever whether
        # Bing is the target. Point search_url anywhere else and the page
        # scrape runs, exactly as intended.
        if "bing.com" in urllib.parse.urlparse(url).netloc:
            feed = self._search_via_feed(query, limit)
            if feed:
                self.payload[key] = {"query": query, "results": feed}
                self._log("SEARCH", "%r -> %d result(s) via rss"
                                    % (query, len(feed)), s)
                return self._searched(key, query, feed, "bing.com")

        self._cmd_goto({"url": url, "wait_until": "domcontentloaded"})

        # Engines redirect after domcontentloaded, which destroys the execution
        # context mid-read. Let it settle, and read again if it moved anyway.
        results = []
        for attempt in (1, 2):
            try:
                self.page.wait_for_load_state("load", timeout=8000)
            except Exception:
                pass
            try:
                results = self.page.evaluate(
                    self._SEARCH_JS, {"limit": limit, "engine": engine}) or []
                break
            except Exception as e:
                if attempt == 2:
                    self._log("SEARCH", f"Result parse failed: {e}")
                else:
                    self.page.wait_for_timeout(700)

        blocked = False
        if not results:
            try:
                body = (self.page.inner_text("body") or "").lower()
                blocked = any(w in body for w in (
                    "unusual traffic", "not a robot", "captcha",
                    "verify you are human", "confirm this search"))
            except Exception:
                pass

        self.payload[key] = {"query": query, "results": results}
        self._log("SEARCH", f"{query!r} -> {len(results)} result(s)"
                            + (" (challenged)" if blocked else ""), s)
        if blocked:
            return {"status": "error", "key": key, "count": 0, "results": [],
                    "error": (f"{engine} served a human-verification page "
                              f"instead of results. Set web.search_url to a "
                              f"different engine or a search API.")}
        # Same shaping as the feed path, including the relevance
        # check -- see `_searched`.
        return self._searched(key, query, results, engine)


    def _cmd_extract(self, args):
        """Universal text/data extraction from the DOM. Handles multiple matches and unified payload elements."""
        s = time.time()
        selector = args.get("selector", "body")
        target_key = args.get("payload_key", f"data_{len(self.payload)}")
        attr = args.get("attr")
        
        js_check = "(s) => window.IAC_PAYLOAD && window.IAC_PAYLOAD[s] instanceof Node"
        if self.page.evaluate(js_check, selector):
            data = self.page.evaluate("(args) => { const el = window.IAC_PAYLOAD[args.s]; return args.attr ? el.getAttribute(args.attr) : el.innerText; }", {"s": selector, "attr": attr})
            count = 1 if data is not None else 0
        else:
            locator = self.page.locator(selector)
            count = locator.count()
            if count == 0:
                self._log("EXTRACT", f"Failed: No elements for {selector}", s)
                return {"status": "error", "error": f"No elements found: {selector}"}
            if count > 1:
                data = [locator.nth(i).get_attribute(attr) for i in range(count)] if attr else locator.all_inner_texts()
            else:
                data = locator.get_attribute(attr) if attr else locator.inner_text()
        
        self.payload[target_key] = {"input_data": data, "type": "extracted_data", "count": count}
        self._log("EXTRACT", f"Key: {target_key} | Items: {count}", s)
        return {"status": "ok", "key": target_key, "count": count, "data": data}

    def _cmd_modify(self, args):
        """Directly update an element's text, value, or attribute. Supports unified payload elements."""
        s = time.time()
        selector = args.get("selector")
        if not selector: return {"status": "error", "error": "Missing selector"}
        
        js_check = "(s) => window.IAC_PAYLOAD && window.IAC_PAYLOAD[s] instanceof Node"
        if self.page.evaluate(js_check, selector):
            js = "(args) => { const el = window.IAC_PAYLOAD[args.s]; " \
                 "if (args.text) el.innerText = args.text; " \
                 "if (args.value) el.value = args.value; " \
                 "if (args.attr && args.val) el.setAttribute(args.attr, args.val); }"
            self.page.evaluate(js, {"s": selector, **args})
        else:
            locator = self.page.locator(selector).first
            if "text" in args:
                locator.evaluate(f"(el, val) => el.innerText = val", args["text"])
            if "value" in args:
                locator.fill(args["value"])
            if "attr" in args and "val" in args:
                locator.evaluate(f"(el, args) => el.setAttribute(args.attr, args.val)", {"attr": args["attr"], "val": args["val"]})
            
        self._log("MODIFY", f"Updated {selector}", s)
        return {"status": "ok"}

    def _cmd_analyze(self, args):
        target_key = args.get("payload_key")
        
        # Determine data source
        if target_key and target_key in self.payload:
            info = self.payload[target_key]
        else:
            # If no payload key, snapshot the LIVE view immediately
            path = args.get("src", "temp_vision.png")
            # Refuse to analyse a page that was never loaded.
            #
            # A fresh browser sits on about:blank, so this screenshotted pure
            # white and sent it to a vision model, which spent twelve seconds
            # writing a thoughtful description of a white rectangle. Twice.
            # The agent then had no idea why it had learned nothing and tried
            # again. An empty page is a precondition failure, not a picture,
            # and saying so costs nothing and takes no time.
            current = ""
            try:
                current = self.page.url or ""
            except Exception:
                current = ""
            if (not current) or current.startswith("about:") or current == "chrome://newtab/":
                self._log("VISION", "No page loaded; nothing to analyse")
                return {"status": "error",
                        "error": "No page is loaded, so there is nothing to "
                                 "analyse. Call web.goto with a URL first, "
                                 "then analyse or extract from it."}
            self.page.screenshot(path=path)
            info = {"src": path}
            self._log("VISION", f"Captured {path} for analysis")
        
        # Merge prompt/config
        local_ai = self.ai_cfg.copy()
        local_ai.update(args)
        
        # Snapshot keys before AI process to calculate diff
        before_keys = set(info.keys())
        
        # Execute Task (Encoding happens inside here now)
        success = task_ai_process(target_key or "live_view", info, local_ai)
        
        # Cleanup massive strings
        for d_key in local_ai.get("drop", ["encoded"]):
            info.pop(d_key, None)
            
        # Return only what was added/modified
        added_data = {k: info[k] for k in info.keys() if k not in before_keys}
        o_key = local_ai.get("output_key", "raw_output")
        if not added_data and o_key in info:
            added_data = {o_key: info[o_key]} # Catch overwrites
            
        added_data["target"] = target_key or "live_view"
        added_data["status"] = "ok" if success else "error"
        added_data["message"] = added_data.get(o_key, "Analysis complete")
        return added_data

    def _cmd_push(self, args):
        """Universal: Push arbitrary data into the system state."""
        key = args.get("payload_key", f"data_{len(self.payload)}")
        self.payload[key] = args.get("data", {})
        self._log("PUSH", f"Registered {key}")
        return {"key": key}

    def _cmd_eval(self, args):
        """Execute custom JavaScript logic. Consolidates state into window.IAC_PAYLOAD."""
        s = time.time()
        code = args.get("code", "")
        payload_key = args.get("payload_key", f"eval_{len(self.payload)}")
        
        if args.get("file"):
            with open(args["file"], "r", encoding="utf-8") as f:
                code = f.read() + "\n" + code
        
        # Wrapped to handle DOM node serialization, async blocks, and state unification
        wrapper = f"""
        async () => {{
            if (!window.IAC_PAYLOAD) window.IAC_PAYLOAD = {{}};
            
            const result = await (async () => {{ 
                try {{
                    return eval({json.dumps(code)});
                }} catch(e) {{
                    return (new Function({json.dumps(code)}))();
                }}
            }})();
            
            // Store raw result in unified browser state
            window.IAC_PAYLOAD[{json.dumps(payload_key)}] = result;
            
            const serialize = (val) => {{
                if (!val) return val;
                if (val instanceof Node) {{
                    return {{
                        tag: val.tagName,
                        id: val.id,
                        classes: val.className,
                        text: val.innerText?.substring(0, 100),
                        iac_type: "dom_node"
                    }};
                }}
                if (val instanceof NodeList || (Array.isArray(val) && val[0] instanceof Node)) {{
                    return Array.from(val).map(serialize);
                }}
                if (typeof val === 'object' && val !== null) {{
                    const obj = {{}};
                    for (let k in val) obj[k] = serialize(val[k]);
                    return obj;
                }}
                return val;
            }};
            
            return serialize(result);
        }}
        """
        result = self.page.evaluate(wrapper)
        
        if args.get("merge") and isinstance(result, dict):
            self.payload.update(result)
            self._log("EVAL", f"Merged {len(result)} keys into payload", s)
        else:
            self.payload[payload_key] = result
            self._log("EVAL", f"Stored result in {payload_key}", s)
            
        return result

    def _cmd_brain(self, args):
        """Cognitive Sweep: Process all matching items in payload via AI Tools."""
        s = time.time()
        local_ai = self.ai_cfg.copy()
        local_ai.update(args)
        
        prefix = local_ai.get("prefix", "image_")
        tasks = local_ai.get("tasks", ["encode", "ai_process"])
        registry = {"encode": task_encode, "ai_process": task_ai_process}
        
        # WHAT to sweep.
        #
        # `prefix` defaults to "image_", but `web.extract` writes `data_0`,
        # `data_1`... so the chain a model naturally reaches for -- extract,
        # then brain -- matched nothing at all and reported "Processed 0
        # items" while looking like it had run. The plan was right; the two
        # halves just disagreed about the key names.
        #
        # So: honour an EXPLICIT prefix exactly, because a caller that named
        # one meant it. Otherwise, if nothing matches the default, sweep
        # whatever the payload actually holds -- which is the thing the
        # operation was asked to think about.
        explicit_prefix = "prefix" in args
        keys = [k for k, v in self.payload.items()
                if k.startswith(prefix) and isinstance(v, dict)]
        if not keys and not explicit_prefix:
            keys = [k for k, v in self.payload.items() if isinstance(v, dict)]
            if keys:
                self._log("BRAIN", f"No '{prefix}*' items; sweeping "
                                   f"{len(keys)} payload item(s) instead")

        count = 0
        for key in list(keys):
            info = self.payload.get(key)
            if isinstance(info, dict):
                # 1. Duplicate Check
                if get_skip_duplicates_option(local_ai):
                    src = info.get("src")
                    if src:
                        try:
                            if src.startswith("http"):
                                res = requests.get(src, timeout=30)
                                res.raise_for_status()
                                img_bytes = res.content
                            elif os.path.exists(src):
                                with open(Path(src), "rb") as f:
                                    img_bytes = f.read()
                            else:
                                img_bytes = None
                            
                            if img_bytes:
                                try:
                                    from ..utils import is_duplicate
                                except ImportError:
                                    from utils import is_duplicate
                                threshold = get_duplicate_threshold_option(self.ai_cfg)
                                is_dup, current_hash = is_duplicate(img_bytes, _seen_hashes, threshold=threshold)
                                if is_dup:
                                    self._log("BRAIN", f"Discarding duplicate image: {key}")
                                    self.payload.pop(key, None)
                                    continue
                                else:
                                    # Pre-encode so task_encode doesn't download it again!
                                    info["encoded"] = base64.b64encode(img_bytes).decode('utf-8')
                        except Exception as e:
                            self._log("BRAIN", f"Duplicate Check Error for {key}: {e}")

                # 2. Proceed with tasks if not duplicate
                for t_name in tasks:
                    if t_name in registry:
                        registry[t_name](key, info, local_ai)
                count += 1
        
        self._log("BRAIN", f"Processed {count} items", s)
        return {"processed": count}

    def _cmd_scroll(self, args):
        s = time.time()
        direction = args.get("dir", "down")
        amount = args.get("amount", "window.innerHeight")
        duration = args.get("duration", 0)
        
        js_script = """
        async (args) => {
            let distance = args.amount;
            if (typeof args.amount === 'string') {
                try { distance = eval(args.amount); } catch(e) { distance = 800; }
            }
            if (args.dir === 'up') distance = -distance;
            else if (args.dir === 'bottom') distance = document.body.scrollHeight - window.scrollY;
            
            if (args.duration <= 0) {
                window.scrollBy({top: distance, behavior: 'smooth'});
                return;
            }
            
            return new Promise((resolve) => {
                let start = null;
                const startY = window.scrollY;
                
                function step(timestamp) {
                    if (!start) start = timestamp;
                    const progress = timestamp - start;
                    const percent = Math.min(progress / args.duration, 1);
                    
                    window.scrollTo(0, startY + (distance * percent));
                    
                    if (progress < args.duration) {
                        window.requestAnimationFrame(step);
                    } else {
                        resolve();
                    }
                }
                window.requestAnimationFrame(step);
            });
        }
        """
        
        self.page.evaluate(js_script, {"dir": direction, "amount": amount, "duration": duration})
        
        if args.get("wait"):
            self.page.wait_for_timeout(args.get("wait"))
        elif duration == 0:
            self.page.wait_for_timeout(1500)
            
        self._log("ACTION", f"Scrolled {direction}", s)
        return {"status": "ok", "direction": direction}

    def _cmd_tour(self, args):
        s = time.time()
        attribute = args.get("attr", "data-tour")
        duration = args.get("duration", 0) # 0 means adaptive
        wait = args.get("wait", 1000)
        
        js_script = """
        async (args) => {
            // Temporarily disable CSS smooth scroll to prevent conflicts with our manual easing
            const originalScrollBehavior = document.documentElement.style.scrollBehavior;
            document.documentElement.style.scrollBehavior = 'auto';

            const stops = Array.from(document.querySelectorAll(`[${args.attribute}]`))
                .filter(el => {
                    const style = window.getComputedStyle(el);
                    return el.offsetWidth > 0 && el.offsetHeight > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                })
                .map(el => ({ el: el, order: parseInt(el.getAttribute(args.attribute)) || 0 }))
                .sort((a, b) => a.order - b.order)
                .map(item => item.el);
            
            if (stops.length === 0) {
                document.documentElement.style.scrollBehavior = originalScrollBehavior;
                return { status: "no_stops_found" };
            }
            
            for (let i = 0; i < stops.length; i++) {
                const target = stops[i];
                const rect = target.getBoundingClientRect();
                const targetY = Math.max(0, Math.min(document.body.scrollHeight - window.innerHeight, 
                    window.scrollY + rect.top - (window.innerHeight / 2) + (rect.height / 2)));
                
                const startY = window.scrollY;
                const distance = Math.abs(targetY - startY);
                
                // Adaptive duration: ultra-fast for short hops, faster base for long travels
                let travelTime = args.duration || Math.min(5000, Math.max(1000, (distance / 600) * 1000));
                if (distance < 150) travelTime = 400; // Snap-to for close proximity
                
                if (distance > 15) {
                    await new Promise((resolve) => {
                        let start = null;
                        function step(timestamp) {
                            if (!start) start = timestamp;
                            const progress = timestamp - start;
                            const percent = Math.min(progress / travelTime, 1);
                            const ease = 1 - Math.pow(1 - percent, 4); // Quartic Ease-Out
                            window.scrollTo(0, startY + ((targetY - startY) * ease));
                            if (progress < travelTime) {
                                window.requestAnimationFrame(step);
                            } else {
                                resolve();
                            }
                        }
                        window.requestAnimationFrame(step);
                    });
                }
                
                // Standardized cinematic pause for all points (including first/already visible)
                if (args.wait > 0) {
                    await new Promise(r => setTimeout(r, args.wait));
                }
            }
            
            // Restore scroll behavior
            document.documentElement.style.scrollBehavior = originalScrollBehavior;
            return { status: "ok", stops: stops.length };
        }
        """
        
        result = self.page.evaluate(js_script, {"attribute": attribute, "duration": duration, "wait": wait})
        self._log("ACTION", f"Completed Guided Tour ({result.get('stops', 0)} stops)", s)
        return result

    def _wrap(self, cat, args, func):
        s = time.time(); func(); self._log(cat, args.get("selector", "N/A"), s)
        return {"status": "ok"}

    # ─── Filesystem & Execution Operations ────────────────────────────────────

    def _cmd_fs_read(self, args):
        s = time.time()
        path = os.path.abspath(args["path"])
        payload_key = args.get("payload_key", f"file_{len(self.payload)}")

        if not os.path.isfile(path):
            self._log("FS_READ", f"Not found: {path}", s)
            return {"status": "error", "error": f"File not found: {path}"}

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Auto-parse JSON files into dicts
        if path.endswith(".json"):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                pass

        self.payload[payload_key] = content
        size = len(content) if isinstance(content, str) else len(json.dumps(content))
        self._log("FS_READ", f"{os.path.basename(path)} -> {payload_key} ({size} bytes)", s)
        return {"status": "ok", "key": payload_key, "path": path, "size": size}

    def _cmd_fs_write(self, args):
        s = time.time()
        path = os.path.abspath(args["path"])

        # Content source: inline string, or pull from payload
        if "content" in args:
            content = args["content"]
        elif "from_key" in args:
            key_path = args["from_key"].split(".")
            content = self.payload
            for k in key_path:
                if isinstance(content, dict):
                    content = content.get(k)
                else:
                    content = None
                    break
            if content is None:
                self._log("FS_WRITE", f"Key not found: {args['from_key']}", s)
                return {"status": "error", "error": f"Payload key not found: {args['from_key']}"}
        else:
            self._log("FS_WRITE", "No content or from_key specified", s)
            return {"status": "error", "error": "No content source specified"}

        # Serialize dicts/lists to JSON
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2, ensure_ascii=False)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content))

        self._log("FS_WRITE", f"{os.path.basename(path)} ({len(str(content))} bytes)", s)
        return {"status": "ok", "path": path, "size": len(str(content))}

    @staticmethod
    def _apply_command_aliases(cmd, aliases):
        """Rewrite the leading executable of a shell command.

        Models emit `python script.py` because that is what most documentation
        says, but many machines only have `python3` -- or `python` resolves to
        a stub that exits non-zero without running anything. That failure is
        opaque: the agent sees a bad exit code and cannot tell why.

        Hosts declare their own mapping (Aether: config.execution.
        command_aliases) and IAC applies it to the first token only, so paths
        and arguments containing the same word are untouched.
        """
        if not aliases or not isinstance(cmd, str) or not cmd.strip():
            return cmd
        stripped = cmd.lstrip()
        indent = cmd[:len(cmd) - len(stripped)]
        parts = stripped.split(None, 1)
        head = parts[0]
        replacement = aliases.get(head)
        if not replacement:
            return cmd
        rest = f" {parts[1]}" if len(parts) > 1 else ""
        return f"{indent}{replacement}{rest}"

    def _cmd_exec(self, args):
        s = time.time()
        cmd = args["cmd"]
        cwd = os.path.abspath(args.get("cwd", "."))
        timeout = args.get("timeout", 120)
        payload_key = args.get("payload_key", None)

        aliases = args.get("command_aliases") or getattr(self, "command_aliases", None)
        original = cmd
        cmd = self._apply_command_aliases(cmd, aliases)
        if cmd != original:
            self._log("EXEC", f"Alias: {original.split(None, 1)[0]} -> {cmd.split(None, 1)[0]}")

        self._log("EXEC", f"Running: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True,
                timeout=timeout
            )
            output = result.stdout.decode("utf-8", errors="replace").strip() if result.stdout else ""
            err = result.stderr.decode("utf-8", errors="replace").strip() if result.stderr else ""
            code = result.returncode

            if payload_key:
                self.payload[payload_key] = {
                    "stdout": output,
                    "stderr": err,
                    "exit_code": code
                }

            status = "ok" if code == 0 else "error"
            self._log("EXEC", f"Exit {code} | {len(output)} bytes stdout", s)
            return {"status": status, "exit_code": code, "stdout": output[:500], "stderr": err[:500]}
        except subprocess.TimeoutExpired:
            self._log("EXEC", f"TIMEOUT after {timeout}s", s)
            return {"status": "timeout", "cmd": cmd}
        except Exception as e:
            self._log("EXEC", f"Failed: {e}", s)
            return {"status": "error", "error": str(e)}

    def _cmd_plan(self, args):
        """AI-driven planning: inject payload context, get structured output."""
        s = time.time()
        prompt = args["prompt"]
        inject_keys = args.get("inject_keys", [])
        output_key = args.get("output_key", "plan_result")

        # Build context block from payload keys
        context_parts = []
        if isinstance(inject_keys, str):
            inject_keys_str = inject_keys.strip()
            if inject_keys_str.startswith("{") or inject_keys_str.startswith("["):
                try:
                    inject_keys = json.loads(inject_keys_str)
                except Exception:
                    pass
            else:
                inject_keys = [k.strip() for k in inject_keys.split(",") if k.strip()]

        def format_val(val):
            if isinstance(val, (dict, list)):
                return json.dumps(val, indent=2, ensure_ascii=False)
            text = str(val)
            return text[:3000] + "... (truncated)" if len(text) > 3000 else text

        if isinstance(inject_keys, dict):
            for k, v in inject_keys.items():
                val = self.payload.get(v) if (isinstance(v, str) and v in self.payload) else v
                context_parts.append(f"[{k}]:\n{format_val(val)}")
        elif isinstance(inject_keys, list):
            for key in inject_keys:
                if isinstance(key, str):
                    if key in self.payload:
                        val = self.payload[key]
                        context_parts.append(f"[{key}]:\n{format_val(val)}")
                    else:
                        context_parts.append(f"[context]:\n{key}")
                elif isinstance(key, dict):
                    for k, v in key.items():
                        val = self.payload.get(v) if (isinstance(v, str) and v in self.payload) else v
                        context_parts.append(f"[{k}]:\n{format_val(val)}")
                else:
                    context_parts.append(f"[context]:\n{key}")

        context_block = "\n\n".join(context_parts)
        full_prompt = f"{prompt}\n\n--- CONTEXT ---\n{context_block}" if context_parts else prompt

        # Build AI request (text-only, no vision)
        local_ai = self.ai_cfg.copy()
        local_ai.update(args)

        info = {"prompt": full_prompt}
        self._log("PLAN", f"Sending to AI ({len(full_prompt)} chars, {len(inject_keys)} context keys)")

        success = task_ai_process(output_key, info, local_ai)

        if success:
            # Extract the result — task_ai_process stores it in info
            result = info.get(output_key, info)
            # Clean internal keys
            for drop in ["prompt", "encoded"]:
                if isinstance(result, dict):
                    result.pop(drop, None)
                info.pop(drop, None)

            self.payload[output_key] = result
            self._log("PLAN", f"Result stored in '{output_key}'", s)
            return {"status": "ok", "key": output_key}
        else:
            self._log("PLAN", "AI inference failed", s)
            return {"status": "error", "error": "AI inference failed"}

    def _resolve_state_path(self, raw=None):
        """Work out where a session snapshot belongs.

        A blank path is the normal case, not an error case: the sandbox HUD
        submits every argument field whether or not it was filled in, so
        `save_state` with an untouched path arrives as "". abspath("") is the
        CURRENT DIRECTORY, and asking Playwright to write a file onto a
        directory fails with `Permission denied` -- which reads like a
        filesystem problem and sends you looking at ACLs on a folder that was
        writable all along.

        So: blank falls back to whatever the config already nominated, then to
        a sensible default, and a path that names a directory gets a filename.
        """
        candidates = [
            raw,
            self.web_cfg.get("save_storage_state"),
            self.web_cfg.get("storage_state"),
            os.path.join("auth", "session.json"),
        ]
        chosen = next((str(c).strip() for c in candidates
                       if c is not None and str(c).strip()), None)

        path = os.path.abspath(chosen)
        # Names a directory (existing, or written with a trailing separator)?
        # Put the file inside it rather than trying to overwrite it.
        if os.path.isdir(path) or chosen.endswith(("/", "\\", os.sep)):
            path = os.path.join(path, "session.json")
        return path

    def _context_alive(self):
        """Can this context still be asked for its storage state?

        Anything after the sandbox loop may be running against a browser the
        user already closed, or one torn down by a previous `stop`. Asking a
        dead context for storage_state raises out of Playwright, which is a
        confusing way to end a run that already did everything correctly.
        """
        ctx = getattr(self, "context", None)
        if ctx is None:
            return False

        # Playwright's SYNC api is bound to the thread that created it. Calling
        # it from anywhere else raises "Cannot switch to a different thread",
        # which is what teardown running on a cleanup thread produced -- an
        # alarming traceback after a run that had already done everything
        # right. There is no way to satisfy the call from here, so the honest
        # answer is that this context is not usable from this thread.
        owner = getattr(self, "_owner_thread", None)
        if owner is not None and owner != threading.get_ident():
            return False

        browser = getattr(self, "browser", None)
        if browser is not None:
            try:
                if not browser.is_connected():
                    return False
            except Exception:
                return False
        page = getattr(self, "page", None)
        if page is not None:
            try:
                if page.is_closed() and not ctx.pages:
                    return False
            except Exception:
                return False
        return True

    def _cmd_save_state(self, args):
        """Save the current storage state (cookies, local storage) to a JSON file."""
        s = time.time()
        path = self._resolve_state_path(args.get("path"))

        if not self._context_alive():
            msg = (f"Browser session already closed; cannot snapshot to {path}. "
                   f"Run save_state before the session ends.")
            self._log("WARN", msg, s)
            return {"status": "skipped", "path": path, "reason": msg}

        parent = os.path.dirname(path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                self._log("SYSTEM", f"Storage state save FAILED: {e}", s)
                return {"status": "error", "path": path, "error": str(e)}

        # Write beside the target, then swap. A session that took a manual
        # login to obtain must never be replaced by a half-written file
        # because the browser died mid-serialisation.
        tmp = f"{path}.tmp"
        try:
            self.context.storage_state(path=tmp)

            # Never downgrade a real session to an empty one. Because close()
            # saves automatically, an ordinary headless run that happened to
            # start without cookies would otherwise write a cookie-less
            # snapshot over a login that took a human to obtain -- and the
            # next run would start logged out too, with nothing to point at.
            if not args.get("allow_empty"):
                new_cookies = old_cookies = 0
                try:
                    with open(tmp, "r", encoding="utf-8") as f:
                        new_cookies = len(json.load(f).get("cookies") or [])
                except Exception:
                    pass
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            old_cookies = len(json.load(f).get("cookies") or [])
                    except Exception:
                        pass
                if new_cookies == 0 and old_cookies > 0:
                    os.remove(tmp)
                    msg = (f"Refusing to overwrite {path} ({old_cookies} cookies) "
                           f"with an empty session. Pass allow_empty to force it.")
                    self._log("WARN", msg, s)
                    return {"status": "skipped", "path": path, "reason": msg}

            os.replace(tmp, path)
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            hint = ""
            if isinstance(e, PermissionError):
                hint = (f" -- check that '{path}' is not a directory, is not "
                        f"open in another program, and is not read-only.")
            self._log("SYSTEM", f"Storage state save FAILED: {e}{hint}", s)
            return {"status": "error", "path": path, "error": f"{e}{hint}"}

        size = os.path.getsize(path) if os.path.exists(path) else 0
        # Remember that this session already produced a snapshot, so the
        # automatic save in close() does not repeat work the plan did
        # explicitly -- by then the context is usually gone anyway.
        self._state_saved = path
        self._log("SYSTEM", f"Storage state saved to {path} ({size} bytes)", s)
        return {"status": "ok", "path": path, "size": size}

    def _cmd_sandbox(self, args):
        """Hand control to the user. Poll for state changes in the browser."""
        s = time.time()
        self._log("SYSTEM", "Entering Sandbox Mode... Control handed to user.")
        
        # 1. State for Command Queue & Chaining
        self._sandbox_exit = False
        self._sandbox_queue = []   # List of (cmd_id, op, args)
        self._sandbox_results = {} # Map of cmd_id -> result
        self._cmd_counter = 0
        self._sandbox_chain = []   # List of {op, args} for export

        # Merge AI Config with Defaults for HUD visibility
        merged_ai = {**DEFAULT_AI_CONFIG, **self.ai_cfg}
        self.ai_cfg.update(merged_ai)

        # 2. Bridge: Queue commands to avoid Playwright Sync re-entrancy deadlocks
        def py_bridge(op_name, op_args):
            try:
                # The HUD submits every argument field for the selected op,
                # filled in or not, so an untouched box arrives as "". An empty
                # string is not the same as "not supplied": it defeats every
                # `args.get(k, default)` in the codebase, because the key is
                # present. save_state was the visible casualty -- blank path ->
                # abspath("") -> the working directory -> Permission denied --
                # but goto, snap and exec all had the same hole.
                if isinstance(op_args, dict):
                    op_args = {k: v for k, v in op_args.items()
                               if not (isinstance(v, str) and not v.strip())}

                if op_name == "exit":
                    self._sandbox_exit = True
                    return {"status": "ok", "msg": "Exiting..."}
                
                if op_name == "get_payload":
                    return {"status": "ok", "data": self.payload}
                
                if op_name == "get_chain":
                    return {"status": "ok", "data": self._sandbox_chain}

                if op_name == "clear_chain":
                    self._sandbox_chain = []
                    return {"status": "ok"}

                if op_name == "get_result":
                    cmd_id = op_args.get("cmd_id")
                    if cmd_id in self._sandbox_results:
                        res = self._sandbox_results.pop(cmd_id)
                        # Ensure status is always present for the HUD
                        if isinstance(res, dict) and "status" not in res:
                            res["status"] = "ok"
                        return {"status": "ok", "data": res}
                    return {"status": "pending"}

                # Queue the command for the main thread to handle
                self._cmd_counter += 1
                cmd_id = str(self._cmd_counter)
                self._sandbox_queue.append((cmd_id, op_name, op_args))
                return {"status": "queued", "cmd_id": cmd_id}
            except Exception as e:
                return {"status": "error", "msg": str(e)}

        try:
            self.page.expose_function("IAC_EXECUTE", py_bridge)
        except: pass 

        # 3. HUD Assets
        ops_metadata = {
            "goto": {"args": ["url"]},
            "snap": {"args": ["path", "payload_key", "full_page"]},
            "analyze": {"args": ["prompt", "payload_key", "model", "host"]},
            "extract": {"args": ["selector", "payload_key", "attr"]},
            "modify": {"args": ["selector", "text", "value", "attr", "val"]},
            "eval": {"args": ["code", "payload_key"]},
            "exec": {"args": ["cmd"]},
            "plan": {"args": ["prompt", "inject_keys"]},
            "wait": {"args": ["ms"]},
            "scroll": {"args": ["dir", "amount"]},
            "tour": {"args": ["attr", "wait"]},
            "brain": {"args": ["prefix", "tasks"]},
            # Modern op names, so a chain exported from here replays unchanged
            # through `python3 iac.py <plan>`.
            "filesystem.read": {"args": ["path", "payload_key"]},
            "filesystem.write": {"args": ["path", "content", "from_key"]},
            "filesystem.copy": {"args": ["src", "dst"]},
            "set": {"args": ["payload_key", "value"]},
            "append": {"args": ["payload_key", "from_key", "sep"]},
            "parse_json": {"args": ["from_key", "into"]},
            "save_state": {"args": ["path"]}
        }

        # Prefill the save path so the single most important sandbox action --
        # log in, keep the session -- needs no typing and cannot be left blank.
        default_state = (self.web_cfg.get("save_storage_state")
                         or self.web_cfg.get("storage_state")
                         or "auth/session.json")
        ops_metadata["save_state"]["defaults"] = {"path": default_state}
        
        hud_js = self._get_sandbox_hud_js(ops_metadata, self.ai_cfg)
        self.context.add_init_script(hud_js)
        try: self.page.evaluate(hud_js)
        except: pass

        # 4. MAIN LOOP: Process queue and poll
        while not self._sandbox_exit:
            try:
                if self.page.is_closed(): break
                
                # SYNC Payload to Browser (Allows window.IAC_PAYLOAD access)
                try:
                    # Strip huge binary/encoded data for browser performance
                    safe_payload = {k: v for k, v in self.payload.items() if not isinstance(v, dict) or "encoded" not in v}
                    # Merge instead of overwrite to preserve live DOM elements
                    js_merge = f"""
                    if (!window.IAC_PAYLOAD) window.IAC_PAYLOAD = {{}};
                    const pyPayload = {json.dumps(safe_payload)};
                    for (const key in pyPayload) {{
                        if (!(window.IAC_PAYLOAD[key] instanceof Node) && !(window.IAC_PAYLOAD[key] instanceof NodeList)) {{
                            window.IAC_PAYLOAD[key] = pyPayload[key];
                        }}
                    }}
                    """
                    self.page.evaluate(js_merge)
                except: pass

                if self._sandbox_queue:
                    cmd_id, op_name, op_args = self._sandbox_queue.pop(0)
                    self._log("SANDBOX", f"Processing Queue: {op_name} (#{cmd_id})")
                    
                    base_op = op_name.split(".")[-1] if "." in op_name else op_name
                    if base_op in self.registry:
                        try:
                            # Record in chain if it's a real operation
                            if op_name not in ["get_payload", "get_result", "get_chain", "clear_chain"]:
                                self._sandbox_chain.append({"op": op_name, "args": op_args})

                            res = self.registry[base_op](op_args)
                            
                            # Ensure result is HUD-safe (Dict with status)
                            if not isinstance(res, dict):
                                res = {"status": "ok", "data": res}
                            elif "status" not in res:
                                res["status"] = "ok"

                            self._sandbox_results[cmd_id] = res
                            self._log("SANDBOX", f"Completed #{cmd_id}: {str(res)[:100]}")
                        except Exception as e:
                            self._sandbox_results[cmd_id] = {"status": "error", "error": str(e)}
                            self._log("ERROR", f"Command #{cmd_id} failed: {e}")
                    else:
                        self._sandbox_results[cmd_id] = {"status": "error", "error": f"Op {op_name} not found"}
                
                self.page.wait_for_timeout(200)
            except Exception as e:
                self._log("WARN", f"Sandbox loop interrupted: {e}")
                break
            
        self._log("SYSTEM", "Exiting Sandbox Mode.", s)
        return {"status": "ok"}

    def _get_sandbox_hud_js(self, ops_metadata, ai_cfg):
        ops_json = json.dumps(ops_metadata)
        ai_json = json.dumps(ai_cfg)
        return """
        (function() {
            if (window !== window.top) return;
            if (document.getElementById('iac-sandbox-hud')) return;

            const ops = """ + ops_json + """;
            const ai_cfg = """ + ai_json + """;
            
            const inject = () => {
                if (!document.body) { setTimeout(inject, 100); return; }
                
                const style = document.createElement('style');
                style.innerHTML = `
                    #iac-sandbox-hud {
                        position: fixed; top: 20px; right: 20px; z-index: 2147483647;
                        background: rgba(10, 10, 10, 0.98); border: 1px solid #00ffcc44;
                        border-radius: 12px; font-family: 'Inter', sans-serif; color: #eee;
                        width: 380px; box-shadow: 0 20px 50px rgba(0,0,0,0.9);
                        backdrop-filter: blur(20px); transition: transform 0.3s ease, opacity 0.3s ease;
                        user-select: none; overflow: hidden; display: flex; flex-direction: column;
                    }
                    #iac-sandbox-hud.minimized { width: 50px; height: 50px; border-radius: 25px; }
                    #iac-sandbox-hud.minimized .hud-content { display: none; }
                    #iac-sandbox-hud.minimized .hud-header { padding: 0; justify-content: center; height: 100%; border: none; }
                    #iac-sandbox-hud.minimized .hud-title { display: none; }
                    
                    .hud-header { 
                        padding: 12px 16px; background: rgba(0, 255, 204, 0.1); 
                        display: flex; align-items: center; justify-content: space-between;
                        cursor: move; border-bottom: 1px solid #333;
                    }
                    .hud-title { font-size: 11px; font-weight: 900; letter-spacing: 2px; color: #00ffcc; text-transform: uppercase; }
                    
                    .hud-tabs { display: flex; background: #111; border-bottom: 1px solid #222; }
                    .hud-tab { 
                        flex: 1; padding: 10px; font-size: 10px; text-align: center; cursor: pointer;
                        opacity: 0.5; transition: 0.2s; border-bottom: 2px solid transparent;
                    }
                    .hud-tab.active { opacity: 1; border-bottom-color: #00ffcc; color: #00ffcc; background: rgba(255,255,255,0.03); }

                    .hud-content { display: flex; flex-direction: column; }
                    .hud-status { 
                        background: #000; padding: 6px 16px; font-size: 9px; 
                        color: #666; border-bottom: 1px solid #222; 
                        display: flex; justify-content: space-between;
                    }
                    .hud-status span { color: #00ffcc; font-weight: bold; }

                    .hud-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; }
                    .hud-pane { display: none; }
                    .hud-pane.active { display: flex; flex-direction: column; gap: 12px; }
                    
                    .hud-select { 
                        background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 8px; 
                        border-radius: 6px; font-size: 12px; width: 100%; outline: none;
                    }
                    .arg-row { display: flex; flex-direction: column; gap: 4px; }
                    .arg-label { font-size: 9px; opacity: 0.5; text-transform: uppercase; letter-spacing: 1px; }
                    .arg-input { 
                        background: #000; border: 1px solid #222; color: #00ffcc; padding: 8px; 
                        border-radius: 4px; font-size: 12px; outline: none;
                    }
                    
                    .hud-footer { padding: 12px 16px; display: flex; gap: 8px; border-top: 1px solid #222; background: rgba(0,0,0,0.3); }
                    .hud-btn { 
                        flex: 1; padding: 10px; border-radius: 6px; border: none; cursor: pointer;
                        font-size: 12px; font-weight: bold; transition: 0.2s;
                    }
                    .btn-run { background: #00ffcc; color: #000; box-shadow: 0 0 15px rgba(0,255,204,0.3); }
                    .btn-run:hover { background: #00ddbb; transform: translateY(-1px); }
                    .btn-exit { background: #ff4444; color: #fff; flex: 0 0 80px; }
                    
                    #hud-console, #hud-payload { 
                        background: #000; color: #00ffcc; font-family: 'Fira Code', monospace; font-size: 10px;
                        padding: 10px; border-radius: 6px; border: 1px solid #222;
                        max-height: 180px; overflow-y: auto;
                        white-space: pre-wrap; word-break: break-all;
                    }
                `;
                document.head.appendChild(style);

                const hud = document.createElement('div');
                hud.id = 'iac-sandbox-hud';
                hud.innerHTML = `
                    <div class="hud-header" id="hud-drag">
                        <div class="hud-title">IAC Terminal</div>
                        <div id="hud-toggle" style="cursor:pointer; font-size:18px;">◈</div>
                    </div>
                    <div class="hud-content">
                        <div class="hud-tabs">
                            <div class="hud-tab active" data-tab="tools">TOOLS</div>
                            <div class="hud-tab" data-tab="payload">PAYLOAD</div>
                            <div class="hud-tab" data-tab="chain">CHAIN</div>
                            <div class="hud-tab" data-tab="logs">LOGS</div>
                        </div>
                        <div class="hud-status">
                            <div>V-CORE: <span>${ai_cfg.vision_model || 'N/A'}</span></div>
                            <div>T-CORE: <span>${ai_cfg.text_model || 'N/A'}</span></div>
                            <div>HOST: <span>${ai_cfg.host || 'local'}</span></div>
                        </div>
                        <div class="hud-body">
                            <div class="hud-pane active" id="pane-tools">
                                <select class="hud-select" id="op-selector">
                                    <option value="">Select Operation...</option>
                                    ${Object.keys(ops).map(k => `<option value="${k}">${k.toUpperCase()}</option>`).join('')}
                                </select>
                                <div id="args-container"></div>
                            </div>
                            <div class="hud-pane" id="pane-payload">
                                <div id="hud-payload">Loading payload...</div>
                            </div>
                            <div class="hud-pane" id="pane-chain">
                                <div id="hud-chain" style="font-size: 9px; color: #aaa; background: #000; padding: 5px; height: 140px; overflow-y: auto;">No steps recorded.</div>
                                <button class="hud-btn" id="btn-export" style="margin-top: 5px; background: #0088ff; color: white;">DOWNLOAD CHAIN</button>
                            </div>
                            <div class="hud-pane" id="pane-logs">
                                <div id="hud-console">System ready.</div>
                            </div>
                        </div>
                        <div class="hud-footer">
                            <button class="hud-btn btn-exit" id="btn-exit">EXIT</button>
                            <button class="hud-btn btn-run" id="btn-run">EXECUTE</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(hud);

                // --- LOGIC ---
                const consoleLog = (msg, isError=false) => {
                    const el = document.getElementById('hud-console');
                    el.innerText = msg;
                    el.style.color = isError ? '#ff4444' : '#00ffcc';
                };

                const updatePayload = async () => {
                    if (hud.classList.contains('minimized')) return;
                    const res = await window.IAC_EXECUTE('get_payload', {});
                    if (res.status === 'ok') {
                        document.getElementById('hud-payload').innerText = JSON.stringify(res.data, null, 2);
                    }
                };
                
                const updateChain = async () => {
                    const res = await window.IAC_EXECUTE('get_chain', {});
                    if (res.status === 'ok') {
                        const el = document.getElementById('hud-chain');
                        if (res.data.length === 0) {
                            el.innerText = 'No steps recorded.';
                        } else {
                            el.innerText = res.data.map((s, i) => `${i+1}. ${s.op}(${JSON.stringify(s.args)})`).join('\\n');
                        }
                    }
                };
                
                setInterval(updatePayload, 3000);

                // Tab switching
                document.querySelectorAll('.hud-tab').forEach(tab => {
                    tab.addEventListener('click', () => {
                        document.querySelectorAll('.hud-tab, .hud-pane').forEach(el => el.classList.remove('active'));
                        tab.classList.add('active');
                        document.getElementById(`pane-${tab.dataset.tab}`).classList.add('active');
                        if (tab.dataset.tab === 'payload') updatePayload();
                        if (tab.dataset.tab === 'chain') updateChain();
                    });
                });

                const opSelector = document.getElementById('op-selector');
                const argsContainer = document.getElementById('args-container');
                
                opSelector.addEventListener('change', () => {
                    const opName = opSelector.value;
                    const op = ops[opName];
                    argsContainer.innerHTML = '';
                    if (!op) return;
                    op.args.forEach(arg => {
                        const row = document.createElement('div');
                        row.className = 'arg-row';
                        let defaultValue = '';
                        if (arg === 'model') {
                            // Specialized model selection
                            if (opName === 'analyze' || opName === 'brain') {
                                defaultValue = ai_cfg.vision_model || ai_cfg.model || '';
                            } else {
                                defaultValue = ai_cfg.text_model || ai_cfg.model || '';
                            }
                        }
                        if (arg === 'host') defaultValue = ai_cfg.host || '';
                        if (arg === 'payload_key') defaultValue = 'sandbox_item';
                        if (arg === 'full_page') defaultValue = 'true';
                        // Per-op defaults supplied by Python (e.g. where the
                        // session snapshot belongs), so the field is never
                        // blank for the actions where blank used to fail.
                        if (op.defaults && op.defaults[arg] !== undefined) {
                            defaultValue = op.defaults[arg];
                        }
                        row.innerHTML = `
                            <label class="arg-label">${arg}</label>
                            <input type="text" class="arg-input" data-arg="${arg}" value="${defaultValue}">
                        `;
                        argsContainer.appendChild(row);
                    });
                });

                document.getElementById('btn-export').addEventListener('click', async () => {
                    const res = await window.IAC_EXECUTE('get_chain', {});
                    if (res.status === 'ok') {
                        const workflow = {
                            iac_version: "2.0",
                            source: "human-sandbox",
                            config: { ai_config: ai_cfg },
                            plan: res.data
                        };
                        const blob = new Blob([JSON.stringify(workflow, null, 2)], {type: 'application/json'});
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `sandbox_workflow_${Date.now()}.json`;
                        a.click();
                    }
                });

                document.getElementById('btn-run').addEventListener('click', async () => {
                    const opName = opSelector.value;
                    if (!opName) return;
                    const args = {};
                    document.querySelectorAll('.arg-input').forEach(input => {
                        args[input.dataset.arg] = input.value;
                    });
                    consoleLog(`[RUN] ${opName}...`);
                    // Switch to logs tab
                    document.querySelector('[data-tab="logs"]').click();
                    
                    try {
                        let res = await window.IAC_EXECUTE(opName, args);
                        
                        // If queued, poll for the result
                        if (res.status === 'queued') {
                            const cmdId = res.cmd_id;
                            let attempts = 0;
                            while (attempts < 120) { // 1 minute timeout
                                await new Promise(r => setTimeout(r, 500));
                                res = await window.IAC_EXECUTE('get_result', {cmd_id: cmdId});
                                if (res.status === 'ok') {
                                    res = res.data; // Unwrap the result
                                    break;
                                }
                                if (res.status === 'error') break;
                                attempts++;
                            }
                        }

                        if (res.status === 'ok') {
                            consoleLog(`[SUCCESS]\\n${JSON.stringify(res, null, 2)}`);
                            setTimeout(updatePayload, 500);
                        } else {
                            consoleLog(`[ERROR] ${res.error || res.msg || 'Unknown error'}`, true);
                        }
                    } catch (e) {
                        consoleLog(`[FATAL] ${e.message}`, true);
                    }
                });

                document.getElementById('btn-exit').addEventListener('click', () => {
                    window.IAC_EXECUTE('exit', {});
                });

                document.getElementById('hud-toggle').addEventListener('click', () => {
                    hud.classList.toggle('minimized');
                });

                // Dragging
                let isDragging = false; let offset = [0, 0];
                const dragHeader = document.getElementById('hud-drag');
                dragHeader.addEventListener('mousedown', (e) => {
                    isDragging = true;
                    offset = [hud.offsetLeft - e.clientX, hud.offsetTop - e.clientY];
                });
                document.addEventListener('mouseup', () => isDragging = false);
                document.addEventListener('mousemove', (e) => {
                    if (!isDragging) return;
                    hud.style.left = (e.clientX + offset[0]) + 'px';
                    hud.style.top = (e.clientY + offset[1]) + 'px';
                    hud.style.right = 'auto';
                });
            };

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', inject);
            } else {
                inject();
            }
        })();
        """

    def run(self):
        import copy
        history = []
        for step in self.workflow:
            op, args = step.get("op"), step.get("args", {})

            # Phase markers — visual separators in the log
            if "_phase" in step:
                print(f"\n{'='*60}\n  {step['_phase']}\n{'='*60}")

            # Support for web. and ai. prefixes in pipeline ops
            base_op = op.split('.')[-1] if '.' in op else op
            
            if op in self.registry:
                res = self.registry[op](args)
                history.append({"op": op, "data": copy.deepcopy(res)})
            elif base_op in self.registry:
                res = self.registry[base_op](args)
                history.append({"op": op, "data": copy.deepcopy(res)})
            elif op:
                self._log("WARN", f"Unknown op: {op}")
        
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump({"history": history, "final_payload": self.payload}, f, indent=4)
        self._log("SYSTEM", f"Saved to {self.output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("config"); args = parser.parse_args()
    agent = WebAgent(args.config)
    try:
        agent.run()
    finally:
        # close() honours save_storage_state for every entry point.
        agent.close()
        os._exit(0)