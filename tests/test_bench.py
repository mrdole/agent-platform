import json
import textwrap

import pytest

import bench_core as bc


def test_is_query():
    assert bc.is_query("*IDN?")
    assert bc.is_query("  FREQ:CENT? ")
    assert bc.is_query("MEAS:VOLT:DC? 10,0.001")
    assert not bc.is_query("FREQ 1GHZ")
    assert not bc.is_query("FREQ?;OUTP ON")
    assert not bc.is_query("FREQ?\nOUTP ON")
    assert not bc.is_query("")


def test_write_allowed_rules():
    sg = {"name": "sg-01", "read_only": False, "write_allowlist": ["FREQ", "POW"]}
    assert bc.write_allowed(sg, "FREQ 1GHZ", True) == (True, "")
    assert bc.write_allowed(sg, "freq   2GHZ", True)[0]
    assert not bc.write_allowed(sg, "FREQ 1GHZ", False)[0]           # session flag off
    assert not bc.write_allowed(sg, "OUTP ON", True)[0]              # not allowlisted
    assert not bc.write_allowed(sg, "FREQ:MODE LIST", True)[0]       # exact header, not prefix
    assert not bc.write_allowed(sg, "FREQ 1GHZ;OUTP ON", True)[0]    # chained
    assert not bc.write_allowed({**sg, "read_only": True}, "FREQ 1GHZ", True)[0]
    assert not bc.write_allowed(sg, "FREQ?", True)[0]                # query via write


SIM = textwrap.dedent("""\
    spec: "1.1"
    devices:
      sg:
        eom:
          TCPIP INSTR:
            q: "\\n"
            r: "\\n"
        dialogues:
          - q: "*IDN?"
            r: "ACME,SG-1000,1234,1.0"
        properties:
          frequency:
            default: 1000000.0
            getter:
              q: "FREQ?"
              r: "{:.1f}"
            setter:
              q: "FREQ {:.1f}"
            specs:
              type: float
    resources:
      TCPIP0::192.0.2.11::inst0::INSTR:
        device: sg
    """)


@pytest.fixture
def sim_bench(knowledge_root, tmp_path):
    pytest.importorskip("pyvisa_sim")
    sim = tmp_path / "sim.yaml"
    sim.write_text(SIM)
    log = tmp_path / "audit.jsonl"
    return bc.Bench(knowledge_root, bench="bench-a", allow_write=True, backend=f"{sim}@sim", audit_log=log), log


def test_sim_identify_query_write_and_audit(sim_bench):
    bench, log = sim_bench
    assert bench.identify("sg-01")["idn"] == "ACME,SG-1000,1234,1.0"
    bench.write("sg-01", "FREQ 2500000.0")
    assert bench.query("sg-01", "FREQ?")["response"] == "2500000.0"
    with pytest.raises(bc.BenchPolicyError):
        bench.write("sg-01", "OUTP ON")
    with pytest.raises(bc.BenchPolicyError):
        bench.query("sg-01", "FREQ 1")
    with pytest.raises(bc.BenchPolicyError):
        bench.write("sa-01", "FREQ 1")  # read_only device
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["ok"] for r in records] == [True, True, True, False, False, False]
    assert records[3]["command"] == "OUTP ON"


def test_unknown_device_and_bench_scope(knowledge_root):
    bench = bc.Bench(knowledge_root, bench="bench-b")
    with pytest.raises(bc.BenchPolicyError):
        bench.device("sa-01")
