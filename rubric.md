### How a company gets its score

1. The agent reads the company's **Business section (Item 1)**. Whether MD&A (Item 7) is added depends on the test run.
2. It lists every qualifying **activity** it finds as an evidence record (see "Evidence records").
3. It scores each of the four themes from **0 to 5** using the ladders below, and marks each theme's **evidence coverage** as `sufficient` or `insufficient`.
4. The code turns the four theme scores into one **final score out of 100**:

| Theme | Weight |
|---|---|
| Creativity, art and storytelling | 35% |
| Community, education and collaboration | 30% |
| Cross-cultural connection and inclusion | 20% |
| Entrepreneurship (enabling founders and small businesses) | 15% |

> **Final score = (Creativity ÷ 5 × 35) + (Community ÷ 5 × 30) + (Cross-cultural ÷ 5 × 20) + (Entrepreneurship ÷ 5 × 15)**
>
> Example: Creativity 4, Community 3, Cross-cultural 2, Entrepreneurship 3 → 28 + 18 + 8 + 9 = **63 / 100**

5. Results always show **all four theme scores and their coverage next to the total**, so a high total can't hide a weak theme. There is no minimum score required on any single theme.
6. Companies are ranked **within their sector**, so banks are compared with banks.

The screener is a values filter only. It helps choose among stocks that already fit the financial plan. It never makes up for weak finances or high valuation, and it never outweighs safely funding the $50,000 yearly payments for 2033–2042.

### Rules that apply to every theme

1. **Score actions, not claims.** Mission statements, value statements and "we are committed to…" language can never score above **1**.
2. **Only current activities count.** Each activity gets a status: `current` (operating during the reporting year), `past` (ended or discontinued), or `planned` (announced but not yet operating). Only `current` activities raise a score. Past and planned ones are recorded for context only.
3. **Judge by scale and reach, not count.**
   - Several small activities still score **2**.
   - One large activity with real reach can score **3 or 4**.
   - Use the significance indicators below.
4. **Significance indicators.** Use whatever the filing actually reports:
   - number of people reached or participants
   - dollars spent or committed
   - number of customers, users or sellers
   - revenue or segment share
   - whether it is a named, ongoing program
   - how central management says it is to the business

   **Don't invent number thresholds.** When a filing gives no figures, judge from its description, and when torn, pick the lower level.
5. **No double counting.**
   - One activity counts **once per theme**, even if the filing mentions it in several places.
   - It can support a second theme only if it independently meets that theme's criteria. For example, an arts-education program for local students fits both Creativity and Community.
6. **Conflicting evidence.** Current activities outweigh past ones, and both outweigh plans. If the filing says a program was cut back or discontinued, score what's operating now, and note the contradiction in `notes`.
7. **When torn between two levels, pick the lower one.**
8. **Be politically neutral.** Two companies doing the same thing get the same score, whether or not they use labels like "DEI." A label with no practice behind it scores at most 1.

### Evidence records and quotes

Every qualifying activity becomes one record:

| Field | What it holds |
|---|---|
| `id` | E1, E2, … (unique within the company) |
| `activity` | One-line plain description of what the company does |
| `quote` | Short exact excerpt (about 40 words max) that shows the activity |
| `section` | Item and nearest heading (for example, "Item 1 > Human Capital > Developing employees") |
| `status` | `current`, `past`, or `planned` |
| `indicators` | Any figures given (people, dollars, customers, revenue), or "none disclosed" |
| `themes` | Which theme(s) it supports, and for each one, the criterion it meets |

**Quote rules:**
- The agent reads the **whole surrounding paragraph** before choosing an excerpt. The excerpt must not mislead out of context. For example, it can't make a pilot program look like a company-wide one.
- The **code** checks that each quote appears word for word in the source. If a quote fails, its evidence record is removed. Any theme that cited it is flagged for human review, and set to 0 if no valid current evidence is left.
- A score of 1 or higher must point to at least one evidence record by `id`.

### Evidence coverage: "not enough info" is not the same as "zero"

For each theme, the agent also marks `coverage`:
- **`sufficient`:** the filing discusses this area enough to judge it. A score of 0 here means the company really shows no qualifying activity.
- **`insufficient`:** the filing barely touches this area, or points to another document for it. Examples: the human capital section is missing, is only a few general sentences, or says "see our website / Annual Report." A low score here may reflect what the company chose to disclose, not what it actually does.

Creativity and Entrepreneurship are usually `sufficient`, because every 10-K has to describe what the company sells. `insufficient` mostly applies to Community and Cross-cultural.

**Second look (the same rule for every company, set in advance):** any company with `insufficient` coverage on Community or Cross-cultural gets an automated second look:
- The agent finds the company's latest sustainability report on its own website.
- It **rescores those themes from scratch** using the 10-K and the report together, with this same rubric.
- Scores can go **up, down, or stay the same**.
- The score source is labeled `"10-K + sustainability report"`.
- If no report is found, the 10-K score stays, coverage stays `insufficient`, and the team reviews the company before ranking it last.

### Theme 1: Creativity, art and storytelling (35%)

**What it means:** the company creates original creative work, or sells tools and platforms that let other people create it, or directly supports artists, writers, designers and storytellers.

There are three kinds of creative involvement. Only the first two count above 1:

| Kind | Example | Counts? |
|---|---|---|
| **Creates** creative work | Publishing, film, music, games, animation | Yes |
| **Enables** others' creative work | Creative software, marketplaces for creators, art supplies, paying or licensing creators | Yes |
| **Uses** creative work as a routine input | In-house designers, marketing content, product design | At most 1 |

Arts sponsorships and arts-education programs count as specific activities, at levels 2–3 depending on their reach.

| Score | What the 10-K shows |
|---|---|
| 0 | Enough info, and no creative activity |
| 1 | Only general language, or creative work used only as a routine business input |
| 2 | Specific current creative activity, limited in scale or reach (for example, a single arts sponsorship) |
| 3 | Specific current activity with substantial scale or reach shown by indicators (for example, a named arts-education program with participant numbers, or a creator product line that is a real but minor part of the business) |
| 4 | Creating or enabling creative work is a **major** part of the business: a reported segment, product line or customer group with disclosed revenue, users or customers |
| 5 | Creating or enabling creative work **is** the core business |

**Traps (these don't count):** "state-of-the-art"; "design" meaning engineering or chip design; "licensing" of patents or drugs; "content" meaning recycled content or web content in general; "storytelling" as brand marketing.

### Theme 2: Community, education and collaboration (30%)

**What it means:** the company teaches, mentors and develops people, especially people **outside** the company, and partners with schools, libraries, nonprofits and communities.

Every Community activity is tagged with one of three kinds:
- **Employee development:** training and mentoring the company's own staff.
- **External education:** apprenticeships, scholarships and skills programs for people outside the company.
- **Community investment:** grants, partnerships, and volunteering with numbers.

**Cap:** if all of a company's activities are employee development, Community is capped at **2**, however large they are. Laura's residency is about teaching and collaborating with others. A score of 3 or more needs external education or community investment, or education as a business.

| Score | What the 10-K shows |
|---|---|
| 0 | Enough info, and no qualifying activity |
| 1 | Only general language ("we invest in our people and communities"), or required compliance training only |
| 2 | Specific current activity, limited in reach; **or** employee development of any size |
| 3 | External education or community investment with substantial reach shown by indicators (named program, participants, dollars) |
| 4 | Very large, measured external education or community commitment central to strategy, **or** education is a major business line |
| 5 | Education, learning or community-building **is** the core business |

**Traps (these don't count):** "community bank"; "the investment / financial / medical / scientific community"; "collaboration agreement" or "partnership" meaning a licensing deal or a legal structure; "training" meaning AI model training or compliance training; "educating customers" as marketing.

### Theme 3: Cross-cultural connection and inclusion (20%)

**What it means:** the company removes barriers so people across cultures, languages and backgrounds can participate, including immigrants, Asian American and LGBTQ+ communities, people with disabilities and other underrepresented groups. It's scored on **what the company does**, not what it calls it.

Every activity is tagged with one of three kinds:
- **Internal practices:** employee networks or resource groups, inclusive benefits, accessible workplaces.
- **Customer access:** accessibility features, multilingual products and services, services built for immigrant or underserved customers.
- **Connecting communities:** programs that actively bring cultures or communities together.

**Workforce demographics alone are a disclosure, not an activity,** so they score 1 at most. **International operations, sales or offices alone don't count.**

| Score | What the 10-K shows |
|---|---|
| 0 | Enough info, and no qualifying activity |
| 1 | General statements, equal-opportunity boilerplate, or demographic statistics only |
| 2 | Specific current activity, limited in reach (for example, employee networks with no figures, or customer support in several languages) |
| 3 | Specific current activity with substantial reach shown by indicators (for example, employee networks with membership figures, or a named accessibility or community program with numbers) |
| 4 | Customer access or connecting communities is a **major** part of the business, with measured reach |
| 5 | Connecting people across cultures, or serving immigrant or underserved communities, **is** the core business |

**Traps (these don't count):** "representations and warranties"; "equity" meaning stockholders' equity; "identity" in identity management, brand identity or identity theft; "inclusion" in an index; "belonging to"; "diversified" revenue; selling in Asia or other foreign markets.

### Theme 4: Entrepreneurship (15%)

**What it means:** the company **enables** other people to start and grow their own ventures: founders, small businesses and independent creators. It does **not** mean the company's own innovation, and **simply having small-business customers is not enough.**

There are two kinds of involvement. Only the second counts above 1:
- **Serving** small businesses: having them as customers or account holders. This counts as 1 at most.
- **Enabling** them: a specific service, program or tool that lowers barriers to starting or growing a business, such as startup financing, affordable payment or merchant tools, seller marketplaces, grants or accelerators.

| Score | What the 10-K shows |
|---|---|
| 0 | Enough info, and no qualifying activity |
| 1 | The company's own innovation, an "entrepreneurial culture," or small-business customers with no specific enabling service |
| 2 | Specific current enabling service or program, limited in reach |
| 3 | Specific current enabling service or program with substantial reach shown by indicators (for example, a named grant or lending program with numbers of businesses helped) |
| 4 | Enabling founders, small businesses or independent creators is a **major** business line, with disclosed revenue, users or customers |
| 5 | Enabling founders, small businesses or independent creators **is** the core business |

**Traps (these don't count):** "innovation" and "innovative"; "entrepreneurial spirit"; "joint venture"; "startup costs"; "small business" mentioned only as a risk or competitor.

### What the agent outputs for each company

```json
{
  "ticker": "XXXX",
  "sector": "Financials",
  "fiscal_year": 2025,
  "sections_read": ["Item 1"],
  "evidence": [
    {
      "id": "E1",
      "activity": "Employee networks open to all staff",
      "quote": "exact excerpt from the filing",
      "section": "Item 1 > Human Capital Resources > Talent, Inclusion and Opportunity",
      "status": "current",
      "indicators": "11 networks, 330,000 memberships",
      "themes": {"cross_cultural": "internal practice with membership figures"}
    }
  ],
  "themes": {
    "creativity":       {"score": 0, "coverage": "sufficient",   "evidence_ids": [],     "reason": "one sentence naming the level matched"},
    "community":        {"score": 0, "coverage": "sufficient",   "evidence_ids": [],     "reason": ""},
    "cross_cultural":   {"score": 0, "coverage": "insufficient", "evidence_ids": ["E1"], "reason": ""},
    "entrepreneurship": {"score": 0, "coverage": "sufficient",   "evidence_ids": [],     "reason": ""}
  },
  "notes": "contradictions, discontinued programs, anything unusual"
}
```

The **code**, not the agent, does five things:
- checks the quotes
- removes failed evidence
- checks that every score of 1 or higher points to `current` evidence
- calculates the final score out of 100
- ranks companies within each sector
