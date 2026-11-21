"""The local writing quality eval: real model, trajectory checks and a judge.

It runs each case twice — with the writing skill and without it — and reports
the paired metrics, the judge's four dimensions, and how much the skill moved
them. It is local only: CI runs the deterministic evals instead.
"""
