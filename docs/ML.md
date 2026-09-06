# Machine-learning references & the forecast plug-in

The decision brain blends several **transparent** sources into one conviction
score. One of those sources is `forecast` — a *predicted* forward return — and it
is the natural place to plug in a Hugging Face model. Swapping the model never
changes the architecture; the model just produces a `Forecast(expected_return,
confidence)` that the conviction engine weights like any other source.

## How the current forecast source works

`app/ml/forecast.py`:

- **`NaiveDriftForecaster` (default, no dependencies).** Estimates the forward
  return from recent drift and sets confidence from the drift's statistical
  significance vs. noise (a t-stat). Honest and cheap, but it's still just
  momentum — it does not *learn*.
- **`HFForecaster` (optional, local).** Wraps a Hugging Face time-series
  foundation model. Lazy-loaded; if `torch`/the model package/weights aren't
  present it silently falls back to naive, so nothing breaks.

Choose it in `config/config.yaml`:

```yaml
agent:
  forecast_model: naive      # or: chronos | chronos-2 | timesfm | kronos | <hf-model-id>
```

## Hugging Face models worth referencing

Time-series **foundation models** (zero-shot forecasting — no training needed):

| Model | HF id | Notes |
|---|---|---|
| **Kronos** (finance-specific) | `NeoQuasar/Kronos-small` | Pretrained on **OHLCV candles** from 45+ exchanges — the most directly relevant to equities. |
| **Amazon Chronos-2** | `amazon/chronos-2` | 120M, SOTA zero-shot among public models; multivariate + covariates. |
| **Amazon Chronos** (T5) | `amazon/chronos-t5-small` | Smaller, easiest to run; good starting point. |
| **Google TimesFM 2.5** | `google/timesfm-2.5-200m-pytorch` | Strong on equity tasks (AAPL/JPM/GOOG) in recent benchmarks. |
| **Salesforce Moirai 2.0** | `Salesforce/moirai-2.0-*` | Also strong average ranks on financial series. |

Financial **sentiment** (to replace the lexicon in `app/intel/sentiment.py`):

| Model | HF id | Notes |
|---|---|---|
| **FinBERT** | `ProsusAI/finbert` | Fine-tuned on financial text; ~4–5% higher F1 than generic methods for movement prediction. |

## Running an HF model locally (not in restricted/CI environments)

```bash
pip install torch chronos-forecasting          # for Chronos / Chronos-2
# or the package each model documents (timesfm, uni2ts for Moirai, kronos repo)
```
Then set `forecast_model: chronos` and launch. First run downloads the weights
(100 MB–GBs). CPU inference is fine for small models; a GPU helps the larger ones.

## Honest caveats (read before trusting any of these)

- Foundation TS models are **competitive, not magic** on noisy equity returns;
  several studies show them roughly on par with good econometric baselines.
  Kronos (finance-native) is the most promising, but still no guaranteed edge.
- **Validate on real history first.** Any ML signal must be backtested on real
  data with strict out-of-sample discipline before it sizes a real order.
  Lookahead bias and overfitting are the usual ways ML "works" in-sample and
  loses money live.
- The conviction engine's **self-correction** will down-weight the forecast
  source automatically if it fails to predict — so a weak model degrades safely
  rather than quietly bleeding the account.
