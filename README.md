# Report Builder

1-page case-study generator for CTV / mobile ad campaigns. One source → four artifacts.

## Purpose
Primary goal is **GEO (Generative Engine Optimization)** — producing semantic HTML with Schema.org JSON-LD that generative engines can cite. Secondary channels are press release (Word/text) and sales PDF.

## Outputs
| File | Channel |
| --- | --- |
| `case_study_web.html` | Company blog (GEO main channel) |
| `case_study_print.pdf` | Sales 1:1 email attachment (1 page) |
| `press_release.docx` | Press distribution |
| `press_release.txt` | Press distribution (plain) |

## Stack
Streamlit · Supabase (shared DB with `casestudy_dashboard`) · Jinja2 · Playwright · Gemini API · python-docx.

## Setup

```bash
cd report_builder
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
playwright install chromium
cp .env.example .env     # fill in keys
streamlit run app.py
```

Or put the same keys under `.streamlit/secrets.toml` — both are read.

## Layout
```
app.py                 # Streamlit UI
config.py              # env loader
data/                  # Supabase repositories (DMP-overhaul-safe)
ai/                    # Gemini narrative + hero image
render/                # html / pdf / docx / txt renderers
templates/             # Jinja2 templates
assets/css/            # web + print stylesheets
output/                # generated artifacts (gitignored)
```

## Notes
- The data layer is intentionally abstracted because the DMP metric schema is slated for overhaul — templates and prompts consume a normalized `CampaignData` contract, not raw DB columns.
- Blog platform is not yet chosen; the HTML output is platform-agnostic (semantic tags + standalone JSON-LD script tag).
