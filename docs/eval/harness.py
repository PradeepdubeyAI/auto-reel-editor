"""Score a retake detector prompt against the labelled set.

Two numbers, reported separately and NEVER averaged into one:
  FALSE CUTS  -- must-keep lines that got cut. The hard constraint. Optimise this to zero.
  RECALL      -- must-cut lines that got caught. The soft constraint.

A variant with better recall and ANY new false cut loses. That rule is why two earlier 0.86-recall
variants were rejected -- they cut real content on refrain/bookend decoys.

Model and prompt are passed EXPLICITLY, never read from env mid-run: an earlier harness
monkeypatched a shared env var from a thread pool and silently scored most variants on the wrong
model, producing suspicious exact-0.000 rows.
"""
from __future__ import annotations
import json, sys, pathlib
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pipeline as P
from testset import SCRIPTS


def render(lines: list[str]) -> str:
    return "\n".join(f"[{i:03d}] {t}" for i, t in enumerate(lines, 1))


def run_one(system: str, script, model: str, temperature: float):
    sid, lang, lines, truth = script
    got: set[int] = set()
    err = None
    try:
        out = P._openai_json(system, render(lines), timeout_s=90,
                             temperature=temperature, model=model)
        r = out[0] if isinstance(out, tuple) else out
        for g in (r or {}).get("groups") or []:
            try:
                k = int(g["keep"]); cs = {int(c) for c in (g.get("cut") or [])}
            except (KeyError, TypeError, ValueError):
                continue
            if not (1 <= k <= len(lines)):
                continue
            got |= {c for c in cs if 1 <= c <= len(lines) and c != k}
    except Exception as e:                       # a dead call must not silently read as "no cuts"
        err = f"{type(e).__name__}: {e}"[:90]
    return sid, lang, truth, got, err


def score(name: str, system: str, *, model: str | None = None, temperature: float = 0.2,
          runs: int = 1, verbose: bool = True):
    model = model or P._RETAKE_MODEL
    agg = {}
    for _ in range(runs):
        with ThreadPoolExecutor(max_workers=6) as ex:
            for sid, lang, truth, got, err in ex.map(
                    lambda s: run_one(system, s, model, temperature), SCRIPTS):
                a = agg.setdefault(sid, {"lang": lang, "truth": truth, "got": set(), "err": []})
                a["got"] |= got                  # union across runs: recall-favouring, matches prod
                if err:
                    a["err"].append(err)
    tp = fn = fp = 0
    per_lang = {}
    rows = []
    for sid, a in agg.items():
        t, g = a["truth"], a["got"]
        _tp, _fn, _fp = len(t & g), len(t - g), len(g - t)
        tp += _tp; fn += _fn; fp += _fp
        L = per_lang.setdefault(a["lang"], [0, 0, 0])
        L[0] += _tp; L[1] += _fn; L[2] += _fp
        rows.append((sid, a["lang"], sorted(t), sorted(g), sorted(g - t), a["err"]))
    rec = tp / max(tp + fn, 1)
    if verbose:
        print(f"\n=== {name}   model={model} temp={temperature} runs={runs}")
        print(f"{'script':20}{'lang':10}{'truth':18}{'got':18}{'FALSE CUTS':>12}")
        for sid, lang, t, g, x, errs in sorted(rows):
            flag = f"  <-- {x}" if x else ""
            print(f"{sid:20}{lang:10}{str(t):18}{str(g):18}{str(x) if x else '-':>12}{flag}")
            for e in errs:
                print(f"{'':20}ERROR {e}")
        print(f"\n  FALSE CUTS {fp}   (hard constraint -- must be 0)")
        print(f"  recall     {rec:.2f}  ({tp}/{tp+fn})")
        for lg, (a, b, c) in sorted(per_lang.items()):
            print(f"    {lg:9} recall {a/max(a+b,1):.2f}  false cuts {c}")
    return {"name": name, "false_cuts": fp, "recall": rec, "tp": tp, "fn": fn}


if __name__ == "__main__":
    print("baseline: the prompt currently shipped")
    score("current _RETAKE_DETECT_SYSTEM", P._RETAKE_DETECT_SYSTEM)
