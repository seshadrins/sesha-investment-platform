# User Guide

A plain-language walkthrough of every screen in the Personal AI Portfolio Manager, written for
someone who invests but isn't fluent in financial jargon. Wherever a term like "ROCE" or "FIFO"
first appears, it's explained in the same breath — there's also a full [Glossary](#glossary) at
the end you can jump to any time.

## What this app is — and isn't

This app helps you **review** your investments and **research** new ones. It never buys, sells,
or places any order with your broker — that's a deliberate design choice, not a missing feature.
Every "recommendation" you see is a prompt to go look more closely at something, not an
instruction to act. You make every real trade yourself, with your actual broker, and then come
back here and record what you did so the app's picture of your holdings stays accurate.

Think of it as a very organized research assistant that never gets tired of re-checking 30 stocks
every morning, remembers exactly why you bought each one, and never forgets to ask "does this
still make sense?"

## The five ideas you'll see everywhere

Before touring the pages, five recurring concepts are worth understanding up front — everything
else builds on these.

**1. Your thesis.** A *thesis* is simply your written-down reason for owning (or wanting to own) a
stock — what you expect to happen, and what would prove you wrong. The app asks you to write one
for every stock you own, with a status:
- **ACTIVE** — you still believe the reason you bought it.
- **WATCH** — something's making you less sure; keep an eye on it.
- **INVALID** — the reason you bought it no longer holds. This is the one status that always
  produces the most serious action prompt (see below), no matter what the price is doing.

**2. Recommendation prompts, not orders.** Every owned stock gets one of six labels, and every
researched-but-not-owned stock gets one of five. These are described in detail in the next two
sections — the short version is: they tell you *what to go look at*, never *what to click.*

**3. Position weight / concentration.** Your *weight* in a stock is simply what percentage of your
total portfolio's value that one stock represents. If you have ₹10 lakh invested and ₹2 lakh of it
is in one company, that stock is a 20% weight. Putting too much in one stock is riskier than
spreading it out — the app watches this for you and flags it before it gets extreme.

**4. Scores out of 100.** Wherever you see a number like "Financial score: 78/100," it's a
transparent, rule-based score built from a company's actual reported financials — not an opinion.
You can always click through to see exactly which numbers produced that score.

**5. Evidence dates and "no data" honesty.** Every piece of information shown has a date attached
— when it was last refreshed, and from where. If something is missing, the app says so explicitly
("Financial evidence not available") instead of guessing or hiding the gap.

## Getting started

### 1. Portfolio Setup

Start here. Create an **account** (a name for where you hold stocks — e.g. your Zerodha or
Groww account), then import your **opening holdings** — what you already own, as of today —
using a CSV template the page provides. This is a one-time step per account; after this, you add
new activity through Transactions.

### 2. Transactions

Every time you actually buy, sell, receive a dividend, or need to correct a holding (an
*adjustment* — e.g. a bonus share credit that isn't a normal buy), record it here. The app keeps
an unchangeable history of every transaction — nothing is ever silently edited, only added to,
so you always have a full, honest record of what happened and when. This matters for one specific
reason: the app calculates your profit using **FIFO** ("first in, first out") — it assumes the
*oldest* shares you bought are the ones sold first, which is how most tax and accounting rules
actually work in practice.

### 3. Prices

The app needs to know what each stock is currently worth to calculate your portfolio's value. You
can type prices in manually, import a CSV of prices, or (if you've set up a free read-only Upstox
account) let the app fetch official daily closing prices automatically. Either way, every price is
stamped with a date — a price from three weeks ago is clearly labeled as such, so you're never
misled into thinking a stale number is current.

## Understanding your Dashboard — "should I buy more, hold, or sell?"

The main page you'll open most often is the **Dashboard**. It has two big tabs:

- **Owned stocks** — everything you actually hold.
- **Prospective · Strong Buy** — stocks you don't own yet that have earned the app's highest
  research rating (explained in the next section).

Inside each tab, the same list of stocks is shown across six smaller tabs, so you never have to
hunt for a stock — pick a stock once (mentally) and read across:

| Tab | What it answers |
|---|---|
| **Portfolio & Thesis** | What do I own, what's it worth, and what did I say my reason was? |
| **Data & Freshness** | How current is the information behind this stock's numbers? |
| **Financial Analysis** | Is this a financially healthy business? |
| **Investor Style Fit** | Does this stock match a proven, rule-based investing style? |
| **Followed Investors** | Are well-known Indian investors buying, holding, or exiting this stock? |
| **Summary & Recommendation** | The one-line bottom-line verdict and why. |

### What each recommendation label actually means

For a stock **you own**, you'll see one of these under "Portfolio & Thesis" and "Summary &
Recommendation":

| Label | Plain-language meaning | What to actually do |
|---|---|---|
| **BUY_MORE** | This position is a small, comfortable slice of your portfolio and hasn't lost much value recently — there's room to add if you still like the story. | Reread your thesis. If you're still convinced, this is a reasonable candidate to add to; if you're not sure anymore, don't just add out of habit. |
| **HOLD** | Nothing about size, losses, or your thesis currently demands action. | Nothing urgent — a normal, healthy state for a position. |
| **TRIM** | Either this position has grown to a size that's risky if it went wrong (concentration), or it's made a large profit and is now large too — a case for taking some money off the table. | Consider selling *part* of the position with your broker, then record the sale here. You don't have to sell all of it. |
| **REVIEW** | Something needs a closer look — a price is missing, a loss has crossed a threshold you set, or your thesis is on Watch. | Open Thesis & Review for this stock and think it through deliberately before doing anything. |
| **SELL** | A meaningful loss has occurred *and* your thesis is already on Watch — two warning signs at once. | Seriously reconsider whether to continue holding; this is a stronger signal than REVIEW. |
| **STRONG_SELL** | You've told the app your own reason for owning this stock no longer holds (thesis = INVALID). This always wins over every other signal, even if the price looks fine. | This is the app's most serious prompt. It exists because *you* said the thesis broke — go decide what to do about it. |

For a stock you're **researching but don't own**, the ratings are different:

| Label | Plain-language meaning |
|---|---|
| **STRONG_BUY** | Passes every check: strong financial score, matches at least two proven investing styles, looks reasonably priced next to similar companies, and has no serious red flag. |
| **BUY** | Good financial score and matches at least one style, without looking clearly overpriced — a notch below Strong Buy. |
| **WATCH** | Not enough going for it yet (weak style match, unclear valuation) — worth revisiting later, not acting on now. |
| **AVOID** | Either the financial score is weak, or there's a serious governance red flag (see below). |
| **REVIEW** | The app doesn't have enough data yet to say anything meaningful — check back once more evidence is available. |

### Thesis & Review — the page where you actually make decisions

This is where you write and update your reasoning for each stock: why you own it, what you expect
to happen (*catalysts*), what could go wrong (*risks*), and — importantly — what specific,
observable thing would tell you that you were wrong (*invalidation conditions*, e.g. "if quarterly
revenue growth falls below 10% for two straight quarters"). Writing this down before you're
emotionally invested in a falling price is far more useful than trying to reason clearly in the
moment.

Every time you save your thesis, the app keeps the previous version too — you can look back and
see how your thinking on a stock evolved, which is one of the most useful habits a long-term
investor can build.

The **Decision journal** on the same page lets you record what you actually decided to do in
response to a recommendation ("Accept," "Reject," "Defer") and why — building an honest record you
can learn from later, including moments you talked yourself out of a good decision, or into a bad
one.

### Financial Analysis — is this a healthy business?

Every stock (owned or not) has a **Financial Analysis** view built from the company's actual
reported numbers. A few terms explained simply:

- **ROCE (Return on Capital Employed)** and **ROE (Return on Equity)** — both answer "how much
  profit does this company generate for every rupee invested in it?" Higher is generally better;
  a business that reliably earns strong returns on the money already inside it is usually a good
  sign.
- **Leverage** — how much of the company is funded by debt versus its own money. Too much debt
  makes a company fragile in a downturn.
- **Cash conversion** — whether the profit the company reports on paper actually shows up as real
  cash. A company that reports profit but never seems to collect the cash is a yellow flag.
- **Governance flags** — specific, evidence-based warnings, such as a company's promoters (the
  founders/controlling owners) selling down their own stake meaningfully in a quarter. These are
  never guesses — each flag names the exact number that triggered it.
- **Valuation vs. sector** — whether the stock is priced cheaply or expensively compared to
  similar companies in the same industry, using standard ratios.

Banks, NBFCs (non-bank lenders), and insurance companies work differently from normal businesses
(their "debt" is customer deposits, for instance), so the app deliberately skips the industrial
leverage/cash-quality checks for them rather than scoring them unfairly with the wrong yardstick.

You can also search for and add a company you don't own yet, purely to research it — this doesn't
create any transaction or affect your real portfolio.

## Finding new stocks — the NIFTY 500 opportunity funnel

### Investor Styles

The app evaluates every stock in the **NIFTY 500** (the 500 largest, most liquid Indian
companies by official index membership) against three named, rule-based investing philosophies:

- **Quality Compounder** — steady, high-quality businesses with strong returns on capital and
  manageable debt, the kind of company you could hold for a decade.
- **Growth at Quality** — faster-growing businesses that still meet a quality bar, not growth at
  any cost.
- **Capital Preservation** — a more conservative style that weighs financial strength and
  low debt more heavily, for someone who cares more about not losing money than maximizing gains.

Each style is a checklist of specific, numeric rules (e.g. "ROCE above 15%"), and for every stock
you can see exactly which rules passed, which failed, and what the actual company number was —
nothing is a hidden black box.

**Backtests**: for each style, the app can show you how stocks that matched this style *in the
past* actually performed afterward — using only information that would genuinely have been known
at the time (annual reports are assumed available 120 days after the company's financial year-end,
matching real-world reporting delays, not an unrealistic instant lookup). Every backtest result
comes with its own honest disclaimer about sample size and what isn't included (like dividends and
trading costs), so you can judge how much weight to give it.

**NIFTY 500 screening**: because checking 500 companies takes time, the app works through the
list gradually in the background (a batch each scheduled morning run) rather than all at once.
Every company it checks — pass or fail — is kept in a dated record, but only the ones that clear
a strict bar (strong financial score, matches at least two styles, reasonable valuation, no
serious governance flag) appear in your **Prospective · Strong Buy** list on the dashboard. Being
in the NIFTY 500 makes a stock *eligible* to be screened — it is not itself a recommendation.

### Followed Investors

Separately, the app tracks the public shareholding disclosures of 15 well-known Indian public
market investors (for example Vijay Kedia, Mohnish Pabrai, Radhakishan Damani, and others) —
sourced from official NSE/BSE regulatory filings, not rumor or social media. For any stock, you
can see whether a followed investor recently:

- **NEW_DISCLOSURE** — just took a new position.
- **INCREASED** — added to an existing position.
- **UNCHANGED** — held steady.
- **REDUCED** — trimmed their position.
- **EXIT_REPORTED** — explicitly no longer holds it (only shown when there's clear evidence of
  this — a name simply disappearing from a list isn't treated as proof of an exit, since investors
  don't have to publicly disclose very small holdings).

Important: **this is supporting evidence, not a signal on its own.** A followed investor buying a
stock never by itself creates a Strong Buy or any other recommendation — it's shown alongside the
financial and style evidence so you can weigh it yourself. Being included in this list is not an
endorsement of that investor's track record; it's simply transparency about what's publicly known.

### Document Analysis

If you have a company's annual report or an earnings call transcript as a PDF, you can upload it
here. The app reads it and drafts a summary, along with the key catalysts, risks, and specific
conditions that would invalidate a thesis on the company — every claim in the draft is linked back
to the exact page/section it came from, so you can verify it yourself in seconds rather than
trusting it blindly. Nothing from this page changes your thesis or recommendation automatically —
you have to explicitly read and accept (or reject) each draft.

## IPOs — should I apply, and how did it do afterward?

The **IPOs** page tracks companies going public, from before listing through their first year on
the exchange.

**Before listing**, the app pulls together official offer-document evidence — the price band, lot
size, how much of the money raised is genuinely new investment into the company versus existing
shareholders cashing out, the company's financial history, debt, any legal disputes, and how the
issue is priced compared to similar already-listed companies. Because a company that hasn't
listed yet has much less of a track record than an established one, the app doesn't apply the same
Strong Buy bar here — instead it gives a more cautious **AVOID**, **WATCH**, or **CONSIDER**
verdict.

**After listing**, for the first year, the app tracks the stock's daily closing price against both
its issue price and the broader market, with structured check-ins around 30, 90, 180, and 365 days
after listing — enough time to see whether the early promise (or early hype) held up. If you did
apply and received an allotment, that holding also shows up normally in your Owned Stocks once you
record the transaction. After a full year of trading history, a company "graduates" into the same
normal research treatment as any other established stock.

## Practicing before risking real money — the Notional Portfolio

This is one of the most useful pages for building trust in the system without any real risk. It's
a **completely separate, simulated account** with its own starting cash — nothing you do here
touches your real holdings or places any real order.

You can:

- Manually buy, sell, add to, or trim a simulated position, or make a cash adjustment.
- **Accept a recommendation** straight from the dashboard as a simulated trade — a one-click way
  to say "if I had followed this Strong Buy / BUY_MORE prompt, what would have happened?" The
  trade only fills once a real, already-observed closing price exists for that date — it never
  uses a price the app couldn't actually have known at the time, which keeps the simulation honest
  rather than flattering.

The Notional Portfolio then shows you, over time: cash, holdings, cost basis, realised and
unrealised return, total value, how spread out (diversified) your simulated positions are, and
drawdown (the largest drop from a peak — a useful measure of how bumpy the ride was, not just
where it ended up). It's also compared side-by-side against your actual portfolio and a
broad-market benchmark, so you can see whether following the app's suggestions would genuinely
have added value versus just holding an index fund.

**Recommendation learning** is the page's most important feature for a novice: it looks back at
every recommendation the app has made and checks, using only real observed prices, how it actually
turned out over the following 3, 6, and 12 months — including how confident the app's own scoring
was, and whether that confidence was actually justified by results. This won't be available the
day you start (there's nothing to learn from yet), but over months of use it becomes a genuine,
evidence-based answer to the question every new user should be asking: **"Should I actually trust
this app's suggestions?"** It's also explicitly honest about what it doesn't yet know — a
recommendation's outcome stays marked "pending" rather than being filled in with a guess until
enough real time has actually passed.

## Reconcile — checking your records match reality

Occasionally, export a holdings statement from your actual broker and upload it here. The app
compares it against what it has calculated from your recorded transactions and shows any
mismatches — a good habit every few months to catch a missed or mistyped transaction before it
throws off your numbers. This is a comparison only; it never changes your recorded transaction
history on its own.

## A simple weekly/monthly rhythm

If you're not sure how often to use this, a reasonable habit:

- **Whenever you actually trade** — record it in Transactions immediately, while it's fresh.
- **Weekly** — open the Dashboard, look at Owned Stocks, and pay attention to anything that isn't
  `HOLD` or `BUY_MORE`. Anything flagged `REVIEW`, `SELL`, or `STRONG_SELL` deserves a few minutes
  of real thought, not a reflexive click.
- **Monthly** — skim Prospective · Strong Buy for new ideas, and check Followed Investors for
  anything interesting in stocks you already care about.
- **Every few months** — run a Reconcile against your broker statement, and revisit the written
  thesis on your largest few holdings — has anything you believed when you bought actually
  changed?
- **Ongoing** — treat the Notional Portfolio's Recommendation Learning tab as a report card for
  the app itself, not just for your stocks.

## Glossary

- **Thesis** — your written reason for owning a stock, plus what would prove it wrong.
- **FIFO (First In, First Out)** — when you sell some of your shares in a company, the app assumes
  you're selling the oldest ones you bought first, for the purpose of calculating profit.
- **Weight / concentration** — the percentage of your total portfolio's value held in one stock.
- **ROCE / ROE** — measures of how efficiently a company turns invested money into profit. Higher
  is generally better.
- **Leverage** — how much of a company is funded by borrowed money (debt) rather than its own.
- **Governance flag** — a specific, evidence-based warning about a company's management or
  ownership behavior (e.g. promoters reducing their stake), never a vague opinion.
- **Valuation** — whether a stock's price looks cheap or expensive relative to its earnings/assets
  and to similar companies, using standard financial ratios.
- **NIFTY 500** — the official list of the 500 largest, most-traded companies on the Indian stock
  market; used here as the pool of candidate stocks to screen.
- **Investor style** — one of three named, rule-based investing philosophies (Quality Compounder,
  Growth at Quality, Capital Preservation) a stock can be checked against.
  Backtest — a test of how a strategy would have performed on real historical data, using only
  information that would genuinely have been available at the time.
- **Followed investor** — one of 15 well-known Indian public-market investors whose official,
  regulatory shareholding disclosures the app tracks as supporting (not decisive) evidence.
- **Notional portfolio** — a simulated, practice-only account with fake money, used to test
  strategies and evaluate the app's own recommendations without any real risk.
- **Drawdown** — the largest drop in value from a previous peak; a way of measuring how bumpy an
  investment's journey was, separate from where it ended up.
- **Benchmark** — a broad market index (like the NIFTY 50) used as a fair comparison point for
  "was this actually a good result, or did the whole market just go up?"
- **DRHP / RHP** — official pre-IPO offer documents (Draft Red Herring Prospectus / Red Herring
  Prospectus) filed with regulators, describing a company going public.
- **Governance scope / limitations** — text the app attaches to almost every result, explaining
  what was and wasn't checked, so a score is never mistaken for a complete picture.

## What this app deliberately does not do

- It never places, modifies, or cancels a real order with any broker. Every trade is yours to
  execute; the app only helps you decide and keep records.
- It does not guarantee returns, and a recommendation is not investment advice — it's a
  transparent, rule-based prompt to think something through, always shown with its reasoning.
- It does not currently calculate or file your taxes.
- Where evidence is missing, stale, or a source couldn't be reached, the app says so directly
  rather than filling the gap with a guess.
