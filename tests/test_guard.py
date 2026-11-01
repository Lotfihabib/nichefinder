"""The Agent-Reach guard hook: public routes only, never Etsy."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).parent.parent / ".claude" / "hooks" / "guard_agent_reach.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize("cmd", [
    'agent-reach web search "site:etsy.com budget spreadsheet"',
    "agent-reach read https://www.etsy.com/listing/123/x",
    "agent-reach web read https://ETSY.com/search?q=x",
    "agent-reach login reddit",
    "agent-reach add twitter",
    "agent-reach browser setup",
    'agent-reach reddit search "x" --backend opencli',
    'agent-reach instagram search "planner"',
    'agent-reach reddit search "budget sheet"',
    'agent-reach twitter search "budget sheet" --limit 5',
    'PATH="$HOME/.local/bin:$PATH" agent-reach read https://www.etsy.com/x',
    '"C:/Users/me/.local/bin/agent-reach.exe" web search "site:etsy.com bestsellers"',
    "agent-reach read https://etsy.me/abc",
    'agent-reach web search "planner site: etsy.com"',
    "agent-reach read https://www.etsy.co.uk/listing/1",
    "agent-reach read https://i.etsystatic.com/x.jpg",
    'cd x && agent-reach login twitter',
])
def test_blocked(cmd):
    assert guard.reason(cmd)


@pytest.mark.parametrize("cmd", [
    'agent-reach youtube search "budget spreadsheet for irregular income" --limit 10',
    'agent-reach reddit search "budget sheet" --limit 10 --backend public-web',
    'agent-reach twitter search "budget sheet" --backend public-web',
    'agent-reach web search "free budget template"',
    "python -m src.signals --phrase x",
    'agent-reach youtube search "small business expense tracker for etsy sellers" --limit 10',
    'agent-reach reddit search "etsy seller bookkeeping" --limit 10 --backend public-web',
    "git status",
    # text that only mentions both words (edits, commits) is not an agent-reach call
    "git commit -m 'Agent-Reach never touches Etsy'",
    "cat >> README.md <<'EOF'\nUse agent-reach setup; it never touches Etsy\nEOF",
])
def test_allowed(cmd):
    assert guard.reason(cmd) is None
