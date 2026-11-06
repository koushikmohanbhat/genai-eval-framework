import importlib.util
import shutil


def test_offline_demo_runs_and_all_checks_pass(tmp_path, ex_dir, monkeypatch):
    work = tmp_path / "ex"
    shutil.copytree(ex_dir, work, ignore=shutil.ignore_patterns(".genai-evals", "calibration.json", "__pycache__"))
    spec = importlib.util.spec_from_file_location("demo", work / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    monkeypatch.chdir(tmp_path)
    demo.OUT = tmp_path / "out"
    demo.HERE = work
    assert demo.main() == 0
    assert (tmp_path / "out" / "3-agent-candidate" / "report.html").exists()
    assert (tmp_path / "out" / "5-online" / "metrics.emf.jsonl").exists()
