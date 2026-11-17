import json
from pathlib import Path
import subprocess
from textwrap import dedent

import pytest

from pydantic import ValidationError

from app.ui_map import MapStore, identity, locator, parse_tree
from app.ui_navigation import NavigationStore
from app.ui_testing import TestCase as UiTestCase


ROOT = Path(__file__).resolve().parents[2]


def execute_node(code, payload, *flags):
    result = subprocess.run(
        ["node", *flags, "--input-type=module", "-e", dedent(code)],
        cwd=ROOT, input=json.dumps(payload), text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_python_and_node_share_hierarchy_fields_and_limits():
    xml = """<hierarchy rotation="1"><node package="demo" class="Row">
      <node resource-id="demo:id/widget_summary" text="42 MB"/>
      <node resource-id="demo:id/title" text=" 输入 &amp; 显示 "/>
      <node class="Switch" enabled="true" checkable="true" bounds="[0,0][32,80]"/>
      <node class="EditText" password="true" text="private" content-desc="hidden">
        <node text="Do not propagate"/>
      </node></node></hierarchy>"""
    trees = [xml, xml.replace("42 MB", "88 MB").replace('rotation="1"', 'rotation="3"')]
    nested = "<hierarchy>" + "<node>" * 81 + "</node>" * 81 + "</hierarchy>"
    invalid = nested.replace("<hierarchy>", "<hierarchy><node>").replace("</hierarchy>", "</node></hierarchy>")
    actual = execute_node("""
        import { readFileSync } from 'node:fs'
        import { parseHierarchy } from './tools/ui-test/hierarchy.mjs'
        console.log(JSON.stringify(JSON.parse(readFileSync(0, 'utf8')).map(xml => {
          try { const {nodes, rotation} = parseHierarchy(xml); return {nodes, rotation}; }
          catch { return null; }
        })))
    """, [*trees, nested, invalid])
    for xml, parsed in zip([*trees, nested], actual):
        tree = parse_tree(xml)
        assert parsed == {"nodes": tree["nodes"], "rotation": tree["rotation"]}
    nodes = actual[0]["nodes"]
    assert nodes[3]["label"] == " 输入 & 显示 " == actual[1]["nodes"][3]["label"]
    assert not nodes[4]["text"] and not nodes[4]["description"] and not nodes[4]["label"]
    assert actual[-1] is None
    with pytest.raises(ValueError):
        parse_tree(invalid)


def test_old_map_seed_replays_against_live_labels(tmp_path):
    store = MapStore(tmp_path)
    source_xml = """<hierarchy><node package="demo" class="Button" resource-id="demo:id/open"
        enabled="true" clickable="true" bounds="[0,0][30,30]">
        <node resource-id="demo:id/summary" text="28 MB"/><node text="详细信息"/>
        </node></hierarchy>"""
    target_xml = '<hierarchy><node package="demo" class="View" text="详细信息"/></hierarchy>'
    source = {**parse_tree(source_xml), "id": "source", "serial": "fixture", "width": 100, "height": 100}
    target = {**parse_tree(target_xml), "id": "target", "serial": "fixture", "width": 100, "height": 100}
    source["nodes"][0]["label"] = "28 MB"  # label stored by the old Python map
    store.remember(source)
    store.remember(target)
    graph = store.graph("fixture")
    graph["pages"] = [
        {"id": item["id"], "name": title, "observationId": item["id"], "identity": identity(item),
         "signature": item["signature"]}
        for item, title in ((source, "首页"), (target, "详细信息"))
    ]
    graph["edges"] = [{"id": "edge", "from": "source", "to": "target", "status": "verified",
                       "locator": locator(source, source["nodes"][0])}]
    store.put("fixture", graph)
    seeds = NavigationStore(tmp_path).load("fixture")["seeds"]
    result = execute_node("""
        import { readFileSync } from 'node:fs'
        import { parseHierarchy, resolveSelector } from './tools/ui-test/hierarchy.mjs'
        import { NavigationMemory } from './tools/ui-test/navigation.mjs'
        const {seeds, sourceXml, targetXml} = JSON.parse(readFileSync(0, 'utf8'))
        let current = parseHierarchy(sourceXml);
        const target = parseHierarchy(targetXml);
        let taps = 0;
        const device = {
          read: async () => current,
          tap: async (selector, validate) => {
            validate(current);
            resolveSelector(current, selector);
            taps++;
            current = target;
          }
        };
        const memory = new NavigationMemory({seeds, device});
        const result = await memory.run("进入详细信息页面", () => { throw Error("unexpected fallback") }, () => false);
        console.log(JSON.stringify({result, taps}));
    """, {"seeds": seeds, "sourceXml": source_xml, "targetXml": target_xml})
    assert result == {"result": "passed", "taps": 1}


def test_branch_and_loop_variables_across_editor_api_and_runner():
    def step(id_, type_, options=None, **fields):
        return dict(id=id_, type=type_, prompt=id_, timeoutMs=1000, options=options, **fields)

    choose = step("choose", "if", condition=step("condition", "assert"))
    yes = step("yes", "assign", {"variable": "result", "valueType": "number", "numberValue": 1},
               parentId="choose")
    no = dict(yes, id="no", branch="else")
    check = step("check", "variable_assert", {"variable": "result", "operator": "eq", "expectedValue": "1"})
    loop = step("loop", "for", {"count": 2, "indexVariable": "index"})
    else_block = step("else", "else", parentId="choose", branch="else")
    image = step("image", "assign", {"variable": "result", "valueType": "image", "baselineId": "a" * 64},
                 parentId="choose")
    second_image = dict(image, id="reference", parentId=None, options=dict(image["options"], variable="reference"))
    compare = step("check", "screenshot_assert", {"firstVariable": "result", "secondVariable": "reference"})

    fixtures = [
        ([choose, yes, check], False, [], None),
        ([choose, dict(yes, enabled=False), no, check], False, [], None),
        ([choose, yes, no, check], True, [], "number"),
        ([choose, yes, else_block, dict(no, parentId="else", branch=None), check], True, [], "number"),
        ([choose, yes, check], True, [{"name": "result", "default": "1"}], None),
        ([choose, yes, dict(check, parentId="choose"), no], True, [], "number"),
        ([choose, yes, dict(check, parentId="choose", branch="else")], False, [], None),
        ([loop, dict(yes, parentId="loop"), check], True, [], "number"),
        ([loop, dict(check, parentId="loop", options=dict(check["options"], variable="index"))], True, [], None),
        ([loop, dict(check, options=dict(check["options"], variable="index"))], False, [], None),
        ([second_image, choose, image, no, compare], False, [], "mixed"),
        ([second_image, choose, image, dict(image, id="no", branch="else"), compare], True, [], "image"),
    ]

    cases = [dict(name="fixture", schemaVersion=1, steps=steps, parameters=params)
             for steps, _, params, _ in fixtures]
    actual = execute_node("""
        import { readFileSync } from 'node:fs'
        import { assignmentsOnEntry } from './client/src/lib/uiSteps.ts'
        import { validateJob } from './tools/ui-test/protocol.mjs'
        console.log(JSON.stringify(JSON.parse(readFileSync(0, 'utf8')).map(testCase => {
          const parameters = Object.fromEntries(testCase.parameters.map(item => [item.name, item.default]));
          let accepted = true;
          try { validateJob({testCase, parameters, serial: "fixture"}); }
          catch { accepted = false; }
          const kind = assignmentsOnEntry(testCase.steps).get("check").get("result") ?? null;
          return {accepted, kind};
        })));
    """, cases, "--import", "tsx")
    for case, (_, accepted, _, kind), observed in zip(cases, fixtures, actual, strict=True):
        assert observed == {"accepted": accepted, "kind": kind}, case
        if accepted:
            UiTestCase.model_validate(case)
        else:
            with pytest.raises(ValidationError):
                UiTestCase.model_validate(case)
