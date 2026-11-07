# Judge calibration labels

The judge-graded grader here is `no_promises` (a policy judge). Before its gate counts, label at least 30 replies
(1 = no promise made, 0 = a promise) in a copy of `no_promises_labels.template.csv` (`case_id,target,label`), then:

```bash
genai-evals calibrate out/run/result.json --grader no_promises --labels labels/no_promises.csv --out calibration.json
```

and uncomment `calibration: calibration.json` in suite.yaml. Re-label after changing judge models or the policy text.
