# Strata cards

Speeds of [Strata](https://github.com/Niko1221/Strata) on real PCs, shared by their owners. Each card is one profile
run of one PC. It has the decode and prompt read speeds with the noise floor of that PC, and the PC in one row with
the limiting component. It also has the decode window split and the settings that differ from the engine defaults.

The site: `https://<owner>.github.io/strata-cards/`. Each card has its own address, `c/<id>/`, with a 1200 x 630
image for link previews.

## Share a card

1. Install [Strata Profile Studio](https://github.com/qiuzan2001/strata-profile-studio) on the PC where Strata
   runs.
2. Make the card: **Share** on a run in the Studio, or `python3 studio.py --card <report folder>`. The Studio shows
   the card and its JSON before anything leaves the PC.
3. Open an issue with the form **Share a card**, and paste `card.json`.

A check reads the JSON (schema and ranges) and saves the card in `data/<id>.json`. Then it builds the site and
answers the issue with the card's address, after about one minute.

## What a card does not have

- the prompts and the answers
- host names, Tailscale names and IP addresses
- user names and file paths
- API keys
- the run config file itself (only the values of the speed settings)
- the engine's start command
- serial numbers and MAC addresses

## Rules

- **The check:** `validate.py` reads each card with `schema/strata-card-1.schema.json` and refuses numbers outside
  physical ranges. A card on the site is "checked: schema and ranges". Nobody reviews it by hand.
- **The limit:** at most 10 cards for each GitHub login in 24 hours. The site does not show the login.
  `data/submitters.json` keeps only a hash of it, for the limit and for removals.
- **The id:** 10 characters from a hash of the card's data. The same data gives the same id, so a card is not added
  twice.
- **Duplicates merge:** cards of the same PC (GPU, VRAM, CPU, RAM, OS), model, quant and settings on the same date
  form one entry. The front page shows their median.
- **Removal:** open an issue with the form **Remove a card** (only for a card that you shared), or a pull request
  that removes `data/<id>.json`.

## Files

| File | What it does |
| --- | --- |
| `data/<id>.json` | the cards (schema `strata-card/1`) |
| `build.py` | builds the site into `_site/`: the front page, one page per card, the comparison page, `data/index.json` |
| `render.py` | the card page, the card image (SVG) and its PNG |
| `validate.py`, `schema/` | the check |
| `submit.py` | the Action's step for one issue: add a card, or remove one |
| `site.js` | the filters of the front page and the comparison page |
| `.github/workflows/card.yml` | the Action: on an issue with the label `card` or `remove`, and on a push to `main` |

Build the site on your own PC: `python3 build.py --out _site`, then open `_site/index.html`. Standard library
only. The PNG needs a headless Chrome or Chromium, or Pillow.

## Licence

MIT. The cards are data that their owners shared for everybody to use.
