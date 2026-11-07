# Judge calibration labels

Judge-graded gates (here: faithfulness, relevance, safe_content) only count once the judges agree with people.

1. Run the suite on the customer's system.
2. Have two SMEs label at least 30 answers (50–100 is better) per judge-graded grader. A label is 1 when the answer
   passes, 0 when it fails. Copy `faithfulness_labels.template.csv` and fill one row per case:
   `case_id,target,label` (`target` may be `*`).
3. `genai-evals calibrate out/run/result.json --grader faithfulness --labels labels/faithfulness.csv --out calibration.json`
4. Uncomment `calibration: calibration.json` in suite.yaml. A judge whose κ confidence interval falls below
   `min_judge_kappa` makes its gates invalid.

Re-label after changing judge models, judge prompts or the rubric.
