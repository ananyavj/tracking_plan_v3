# Tracking Plan Auditor — README

## 🎯 What This Project Does
An autonomous analytics validation engine that cross-references live Amplitude event data against a structured tracking plan (Excel), using AI agents (Claude or Gemini) to flag instrumentation bugs categorized as M0–M6 mistakes.

---

## 🔍 Why Claude and Gemini Report Different Numbers

### The Core Truth About Session IDs
A `session_id` IS consistent. `sess_abc123` in Amplitude always has exactly the same 5 events attached to it, no matter who queries it. That's a fixed truth in the database.

So what's actually different is **NOT the events within a session** — it's **which sessions** each method happened to pull.

### The Library Analogy
Think of Amplitude's database like a library with 40,000 books (events) spread across 5,000 shelves (sessions).

*   **Gemini (Current "Wide-Random" Strategy):** Walked in and grabbed a chronological slice of the first 200 books. It sampled from ~150 different shelves but only grabbed 1–2 books from each shelf. Result: **Wide but shallow.**
*   **Claude (Targeted "High-Signal" Strategy):** Walked in and asked for "Purchase Funnel" books specifically. Because these events co-occur, it pulled from only ~50 shelves but collected the **entire story** for each. Result: **Narrow but deep.**

---

## 🛠️ Current Limitations & Architecture Discrepancies

### "Single-Shot" (Gemini) vs. "Multi-Turn" (Claude)

#### 1. Gemini in Streamlit (The "Single-Shot" Constraint)
In the Streamlit app, we use the **Gemini Free Tier**, which has a strict **250k token-per-minute** limit.
*   **The Problem:** Everything (Rules + Plan + Events) must fit into **one single prompt**. This forces a strict **200-event cap**.
*   **The Sampling Problem:** If a session has 10 events but we are near the 200-event quota, the sampler is forced to **"behead" the session** (deleting half the events) to make it fit. 
*   **The Result:** Gemini receives incomplete fragments. It sees the orders (M1-M3) but misses the preceding events needed to prove M4-M6 logic errors.

#### 2. Claude Desktop (The "Multi-Turn" Advantage)
Claude Desktop is a native agent that can take its time. It doesn't have an "all-or-nothing" token wall.
*   **The Edge:** It can call tools **sequentially**. It fetches 50 orders, analyzes them, and then calls the tool again to pull the *full history* for those specific users.
*   **The Result:** Claude sees the **complete 10-event story** for every user, giving it the evidence needed to fire M4 (Funnel Break) and M6 (User State) errors.

---

## ⚖️ Fundamental Limitation: AI Audit is a "Spot Check"

Regardless of the model, we are auditing **less than 1%** of your data (e.g., 300 events out of 40,000).

1.  **What we WILL catch (Systemic Bugs):** If a price is hardcoded as a string (M1), it breaks for 100% of users. Even a tiny sample will catch this easily. **AI Accuracy: ~100%.**
2.  **What we MIGHT catch (Common Errors):** If a specific button is missing an event, we only find it if our sample happens to include a user who clicked that button. **AI Accuracy: High but Probabilistic.**
3.  **What we WILL miss (Rare Edge Cases):** Bugs happening only on specific device/mode combinations will likely never appear in a 300-event window. **AI Accuracy: Very Low.**

**The Goal:** Use AI to understand **Intent and Context** (e.g., *"This price is missing but that's expected because this user is on a legacy free-trial—you should update your tracking plan rules for this edge case"*), not for 100% data coverage.

---

## ✅ Resolved Issues

*   **Fixed: API Key Security** — All keys moved to `.env` (gitignored). Dirty git history squashed and wiped.
*   **Fixed: AI Branding** — Removed hardcoded "Claude" strings from `SKILL.md`. Gemini reports now correctly identify as `gemini-flash-latest`.

## 🔴 Open Issues (Next Steps)

### Issue 1: Gemini Fetches Shallowly (Logic Fix)
*   **Problem:** `mcp_tools.py` pulls a raw chronological dump. 
*   **Fix:** Rewrite fetch strategy to query per-event-type (mirroring Claude) to reconstruct full sessions.

### Issue 2: 200-Event Hard Cap (Token Limit)
*   **Problem:** Free Tier 250k token limit.
*   **Fix:** Compress `SKILL.md` prompts and strip redundant JSON fields to buy more "event room."

### Issue 3: Single-Call Fragility
*   **Problem:** If Gemini's JSON is slightly malformed, the entire audit fails.
*   **Fix:** Add retry logic and error-correction prompts in `gemini_agent.py`.

---

## 📊 Summary Table

| Issue | Cause | Token Limit? | Code Fixable? | Effort |
| :--- | :--- | :--- | :--- | :--- |
| **AI Audit Accuracy** | Sampling error (0.75% of data) | ✅ Yes | ❌ No | Fundamental |
| **Session "Beheading"** | Single-shot prompt wall | ✅ Yes | ✅ Partly | Medium |
| **Shallow session fetch**| Bulk Export API strategy | ❌ No | ✅ Yes | Medium |
| **200-event cap** | Free tier quota | ✅ Yes | ✅ Partly | Medium |
| **API Keys in Code** | **RESOLVED** | - | - | - |
| **Claude Branding** | **RESOLVED** | - | - | - |

---

**Key takeaway:** AI helps identify the *reasoning* behind errors. The primary technical goal is to move from a **wide-random** sampling strategy to a **high-signal session** strategy to maximize what Gemini can see within its token budget.
