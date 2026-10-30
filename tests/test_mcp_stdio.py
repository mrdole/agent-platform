"""End-to-end: start the MCP servers as OpenCode would (stdio JSON-RPC) and call tools."""
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("mcp")


class Client:
    def __init__(self, cmd, env):
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env)
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server exited: " + self.proc.stderr.read())
            resp = json.loads(line)
            if resp.get("id") == self.next_id:
                return resp

    def start(self):
        init = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "0"}})
        assert "result" in init, init
        self.send("notifications/initialized", notify=True)
        return init

    def call(self, name, args):
        resp = self.send("tools/call", {"name": name, "arguments": args})
        assert "result" in resp, resp
        return resp["result"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def text_payload(result):
    if result.get("structuredContent") is not None:
        sc = result["structuredContent"]
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result["content"][0]["text"])


def test_knowledge_server(root, knowledge_root):
    env = {**os.environ, "KNOWLEDGE_ROOT": str(knowledge_root), "KNOWLEDGE_PROFILE": "it-debug"}
    c = Client([sys.executable, str(root / "mcp/knowledge/server.py")], env)
    try:
        c.start()
        tools = {t["name"] for t in c.send("tools/list", {})["result"]["tools"]}
        assert tools == {"search_docs", "get_doc", "list_docs", "list_devices", "get_device", "find_skill",
                         "list_models", "recommend_model"}
        hits = text_payload(c.call("search_docs", {"query": "--auto approved push"}))
        hits = hits if isinstance(hits, list) else [hits]
        assert hits[0]["path"] == "gotchas/opencode-headless-ask.md"
    finally:
        c.close()


def test_bench_server_lists_tools(root, knowledge_root):
    env = {**os.environ, "KNOWLEDGE_ROOT": str(knowledge_root), "BENCH_NAME": "bench-a"}
    c = Client([sys.executable, str(root / "mcp/bench/server.py")], env)
    try:
        c.start()
        tools = {t["name"] for t in c.send("tools/list", {})["result"]["tools"]}
        assert tools == {"list_devices", "identify", "query", "write"}
        devs = text_payload(c.call("list_devices", {}))
        devs = devs if isinstance(devs, list) else [devs]
        assert {d["name"] for d in devs} == {"sa-01", "sg-01", "board-a"}
        bad = c.send("tools/call", {"name": "write", "arguments": {"device": "sg-01", "command": "FREQ 1"}})
        assert bad.get("error") or bad["result"].get("isError")  # writes are off by default
    finally:
        c.close()
