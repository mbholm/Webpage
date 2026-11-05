# martinbholm.com

Static personal website. No build step: plain HTML, one CSS file, one tiny JS file.

## Structure

```
index.html          Home (bio, projects, recent work)
research.html       Working papers, R&Rs, publications
reports.html        Policy Reports
op-eds.html         Newspaper commentary, grouped by year
normac-2026.html    Conference page
dashboard.html      Norway macro dashboard (+ dashboard.js, dashboard.css, data/dashboard.json)
build_dashboard_data.py  Fetches the dashboard data from SSB, Norges Bank and NBIM
404.html            Not-found page (GitHub Pages picks it up automatically)
style.css           All styling (light + dark mode, mobile, print)
site.js             Builds the mailto link at runtime (anti-spam)
assets/             portrait.jpg, favicon.svg
papers/             All paper PDFs (working papers, appendices, slides, reports)
files/              CV (PDF) and conference program
cv/                 Holm_CV.tex, the LaTeX master file for the CV
build_cv.py         Compiles the CV and copies the PDF to files/Holm_CV.pdf
build_recent.py     Regenerates the "Recent" lists on the home page
CNAME               Custom domain for GitHub Pages
.nojekyll           Tells GitHub Pages to serve files as-is
```

## Editing content

Every page carries its own copy of the sidebar (the `<aside class="sidebar">` block at the top).
If you change the sidebar (title, phone, links), change it in all five pages.

### Adding a paper

Copy one of the `<article class="paper">` blocks in `research.html`, drop the PDF into `papers/`,
and point the title link at it. Extra links (appendix, slides, published version, replication package)
go as plain `<a>` chips inside an optional `<div class="links">`; leave that div out if there are none.

Status badges for R&Rs use `<span class="status">R&amp;R</span>` inside the `.meta` line.

### Updating the CV

The CV has one source file: `cv/Holm_CV.tex`. Edit it, then run

```
python build_cv.py
```

This compiles it with pdflatex and copies the result to `files/Holm_CV.pdf`, which is what the
"Curriculum vitae" link in the sidebar opens. Commit both the `.tex` and the PDF.

- The whole CV is one two-column grid. A dated line is `\entry{2024--}{Text}`; a paper is
  `\paper{2025}{Title}{\textit{Journal}}{A. Coauthor and B. Coauthor}`. Leave an argument empty
  (`{}`) for no date, no venue or no coauthors. Grants use `\grant{years}{title}{details}`.
- When a paper is accepted, move its `\paper` line to Publications and put the year (or
  "Forthcoming") in the first argument.
- When a paper changes status on `research.html`, make the same change in the CV so the two stay in step.
- A Teaching section is at the bottom of the file, commented out. Uncomment it for applications.
- `cv/build/` (LaTeX by-products) and `cv/archive/` (the two pre-merge source files) are ignored by Git.

### The Norway dashboard (dashboard.html)

A one-page dashboard of Norwegian macro indicators (GDP, inflation, unemployment, employment, interest
rates, the krone, trade balance, current account, the oil fund, the government budget, house prices,
productivity, wages and labour shares). Three files:

- `build_dashboard_data.py` fetches every series from Statistics Norway (PxWebApi v2), Norges Bank
  (SDMX API), Eurostat (JSON API), NAV, NBIM and the Ministry of Finance, computes the derived series (TFP, labour productivity,
  labour shares, ratios to GDP, growth rates) and writes `data/dashboard.json`. Each indicator is fetched
  independently; if one source fails, that indicator keeps the previous data and the error is recorded
  in the JSON.
- `dashboard.js` holds the display definitions (titles, descriptions, which series a tile shows, which
  are offered in the detail view) keyed by the same indicator ids, and renders the page.
- `dashboard.css` holds the page's styles, including the chart palette.

The browser cannot query SSB directly (no cross-origin header), so the data is pre-fetched: the deploy
workflow runs `build_dashboard_data.py` on every push **and every morning at 06:20 UTC** (schedule in
`.github/workflows/pages.yml`), so the live site refreshes daily without any commits. The committed
`data/dashboard.json` is the fallback if a fetch fails in CI. To refresh locally:

```
python build_dashboard_data.py
```

Sources without an API are read from the publisher's web page, so they break if the page is redesigned:

- **NAV registered unemployment**: the two Excel files linked from NAV's "Hovedtall om arbeidsmarkedet"
  page (seasonally adjusted main figures, and the history from 1991). The links change every month, so
  the script finds them by file-name prefix (`HARB100`, `HL100`).
- **Norges Bank projections** (policy rate, CPI, CPI-ATE): the data workbook of the latest Monetary
  Policy Report, found from the report index page. Quarterly values are plotted at the middle month of
  each quarter and drawn dashed. If the fetch fails, the previous projections are kept.
- **Oil fund value**: the chart data on NBIM's "The fund's value" page.
- **Government budget**: regjeringen.no. The script opens the budget hub page for the newest year
  (`/no/statsbudsjett/<year>/`), then reads the Excel files behind the figures in Nasjonalbudsjettet
  and the revenue and expenditure table of the state budget. Figures are found by their titles, not
  their numbers, because the numbering changes every year. The tile switches to the new budget by
  itself when it is published in October. The English names of the budget items are in
  `BUDGET_LABELS`. A new item without a translation is shown in Norwegian.
- **Oil fund management costs**: NBIM publishes no data series in kroner. The numbers are a hand-kept
  table at the top of `ind_oil_fund` in the build script (`NBIM_COSTS_REPORTED`), transcribed from the
  annual reports ("Total management costs in Norges Bank were X billion kroner"). **Add one line each
  year** when the annual report comes out in late February. 1998–2010 are estimated from NBIM's cost
  ratio in basis points and are marked as approximate on the page.

Method notes: TFP is computed in growth form (Törnqvist), weighting hours and capital by the labour
share from the national accounts (table 09170), then cumulated into an index. Labour shares follow the
definitions in «Lønnsandelen i industrien»: gross = W/(W + capital consumption + operating surplus),
net = W/(W + operating surplus). The common component for manufacturing is a port of
`pc1_expanding.m` from the paper's replication package and reproduces its published key numbers.

A tile can be linked directly: `dashboard.html#inflation` opens it, and
`dashboard.html#oil_fund=costs` opens it with the named series switched on.

To preview, serve over http (`python -m http.server 8000`); the page loads the JSON with fetch().

The deploy workflow also adds `?v=<commit id>` to the stylesheet and script links in every page, so
browsers fetch the new `.css` and `.js` after each push instead of showing a cached copy. If a page
still looks old right after a push, wait a few minutes (GitHub caches pages for ten) and reload.
To add an indicator: write an `ind_*` function in the build script and a matching entry in `DEFS` in
`dashboard.js`. Table ids and variable codes must be verified against the API metadata, not guessed.

### Adding an op-ed

Copy an `<article class="oped">` line in `op-eds.html` under the right year (newest first).

### The "Recent ..." lists on the home page

These are generated from `research.html` and `op-eds.html`. You never edit them by hand.

- **Recent working papers**: all papers under the "Working papers" and "Revise & resubmit" headings,
  sorted by the "This version: Month YYYY" date (or "First version" if there is no "This version"), top 4.
- **Recent publications**: papers under "Publications", sorted by the year in the meta line, top 4.
- **Recent op-eds**: all entries in `op-eds.html`, sorted by the "Month YYYY" date, top 3.

The numbers and sections are set with `data-limit` and `data-sections` on the `<ul>` elements in `index.html`.

Two mechanisms keep them current:

1. `python build_recent.py` bakes the lists into `index.html`. The GitHub Action in `.github/workflows/pages.yml`
   runs it on every push before deploying, so the live site is always correct. Run it yourself when you want
   to see the result locally by opening `index.html` from disk.
2. `site.js` recomputes the same lists in the browser when the page is served over http (belt and braces;
   it does nothing when the file is opened from disk, because browsers block the cross-file read).

## Deploying to GitHub Pages

### A. First-time setup with GitHub Desktop

1. Install GitHub Desktop from https://desktop.github.com and sign in with your GitHub account (`mbholm`).
2. **File → Add local repository…** → Choose this folder (`10_Webpage`).
   GitHub Desktop will say it is not a Git repository and offer **"create a repository"** — click that link.
   In the dialog: keep the name, leave "Git ignore" and "License" as "None", click **Create repository**.
3. You will now see all files listed as changes. At the bottom left, type a summary such as `New website`
   and click **Commit to main**.
4. Click **Publish repository** (top bar). Name it e.g. `website`, **untick "Keep this code private"**
   (GitHub Pages on a free account needs a public repository), click **Publish repository**.
5. In the browser, open the repository on github.com → **Settings → Pages**:
   - *Build and deployment → Source*: choose **GitHub Actions**.
     The workflow in `.github/workflows/pages.yml` then runs on every push (it also regenerates the home-page lists).
   - Wait a minute, then check the **Actions** tab: the "Build and deploy to GitHub Pages" run should be green.
     The site is live at `https://mbholm.github.io/website/` at this point.
6. Still under **Settings → Pages → Custom domain**, enter `www.martinbholm.com` and click Save.
   (The `CNAME` file in the folder already contains this, so it survives redeploys.)
7. At your DNS provider (wherever martinbholm.com is registered), set:
   - `www`  → CNAME → `mbholm.github.io`
   - apex `martinbholm.com` → A records → `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
   DNS can take up to a few hours. When GitHub shows "DNS check successful", tick **Enforce HTTPS**.
8. Remove the domain mapping from Google Sites so it no longer claims `martinbholm.com`.

### DNS at GoDaddy (step 7 in detail)

1. Log in at godaddy.com → click your name (top right) → **My Products**.
2. Next to **martinbholm.com** click **DNS** (or the three dots → **Manage DNS**).
   You land on the "DNS Records" table.
3. Clean out what Google Sites left behind. Delete (pencil → trash icon) any of these if present:
   - A records with name `@` pointing to Google addresses (`216.239.32.21`, `216.239.34.21`, `216.239.36.21`, `216.239.38.21`)
     or to "Parked" / `WebsiteBuilder Site`.
   - AAAA records with name `@`.
   - The CNAME with name `www` pointing to `ghs.googlehosted.com` — *edit* this one rather than delete, see next step.
   Leave everything else (MX, TXT, NS, `_domainconnect`, etc.) alone.
4. Edit the `www` record (or **Add New Record** if there is none):
   - Type: **CNAME**  · Name: `www`  · Value: `mbholm.github.io`  · TTL: 1 Hour → **Save**.
   (If GoDaddy complains that a record already exists, delete the old `www` CNAME first, then add it.)
5. Add four A records for the bare domain, one at a time (**Add New Record**):
   - Type: **A** · Name: `@` · Value: `185.199.108.153` · TTL: 1 Hour
   - Type: **A** · Name: `@` · Value: `185.199.109.153`
   - Type: **A** · Name: `@` · Value: `185.199.110.153`
   - Type: **A** · Name: `@` · Value: `185.199.111.153`
6. Scroll down to **Forwarding** on the same page. If there is a domain forward set up (Google Sites often
   adds one), delete it. GitHub itself redirects `martinbholm.com` → `www.martinbholm.com` once the A records are in place.
7. Wait 10–60 minutes (GoDaddy is usually quick). Then in the GitHub repository → Settings → Pages,
   click **Check again** next to the custom domain. When it says "DNS check successful", tick **Enforce HTTPS**.
   The certificate can take a further few minutes to an hour to be issued.

You can verify from any computer with:
```
nslookup www.martinbholm.com      # should answer mbholm.github.io + 185.199.x.x addresses
nslookup martinbholm.com          # should list the four 185.199.x.x addresses
```

### B. Publishing an update later

1. Edit the files (e.g. add a paper to `research.html` and drop the PDF in `papers/`).
2. Open GitHub Desktop: the changed files appear on the left.
3. Type a short summary (e.g. `Add new working paper`) → **Commit to main** → **Push origin**.
4. About a minute later the Action has rebuilt and deployed the site. Nothing else to do.

Note: `build_recent.py` runs on the server as part of the deploy, so you do not need to run it locally
before pushing. Run it only if you want the home-page lists to be correct when previewing from disk.

### C. Same thing from the command line (alternative)

```
git init
git add .
git commit -m "New website"
git branch -M main
git remote add origin git@github.com:mbholm/website.git
git push -u origin main
```
Then follow steps 5–8 above. Later updates: `git add . && git commit -m "..." && git push`.

### Dropbox note

This folder lives inside Dropbox. That works, but Dropbox and Git both track the same files, so
avoid editing the site on two computers at the same time. If you ever see odd Git errors, the safe
fix is to clone the repository fresh from GitHub into a folder outside Dropbox.

## Local preview

Open `index.html` directly in a browser, or run `python -m http.server 8000` in this folder and visit http://localhost:8000.
