# Cambridge Politics & Law Events

A personal calendar that gathers politics, law and entrepreneurship events from across Cambridge into one place.

## Run it

```bash
python3 app.py
```

This opens http://localhost:8765. It needs only Python 3.9 or later, with nothing to install. If the event data is more than 6 hours old, the app fetches it again when it starts. You can also press **↻ Refresh data** in the sidebar at any time.

If you just want the data without the app, run `python3 scraper.py`. It writes `events.json` and `events.js`. After that, `index.html` also works when you open it straight from disk.

## The website version (GitHub)

Once this project is on GitHub, the file `.github/workflows/update-and-publish.yml` does two things:

- **Every 6 hours** it runs the scraper on GitHub's computers and saves the fresh `events.json`. Your Mac doesn't need to be on.
- **It publishes the calendar as a website** at `https://YOUR-USERNAME.github.io/cambridge-events/`. On an iPhone, open that address in Safari and tap **Share → Add to Home Screen**.

To fetch events immediately, open the repository on GitHub, go to the **Actions** tab, choose **Update events and publish website**, then click **Run workflow**.

On the website, your starred plan, interests and filters are saved in that phone or browser only. The website can't save termcard events, so add those by editing `termcards.json` on GitHub (open the file and click the pencil icon). The next update picks them up.

GitHub can pause scheduled jobs in repositories that have had no activity for 60 days. If the updates ever stop, go to the **Actions** tab and re-enable the workflow.

## Calendar subscriptions

Each update also writes calendar feeds to the `calendar/` folder: `all.ics`, plus one per topic (`law.ics`, `politics.ics`, `entrepreneurship.ics` and so on). You'll find them under **Subscribe in your calendar** in the app's sidebar.

- **iPhone or Mac:** tap or click a feed, then choose **Subscribe**. Apple Calendar then checks for new events by itself.
- **Google Calendar:** click **copy** next to a feed. In Google Calendar on a computer, go to **Other calendars → + → From URL** and paste the link.

## Sources

| Source | How it's read |
|---|---|
| talks.cam.ac.uk | Searches about 55 keywords covering politics, law, societies and entrepreneurship, finds the series each upcoming talk belongs to, then reads that series' XML feed. Relevant series are included in full. For other series, only talks with a matching title are kept. |
| POLIS, Faculty of Law, Institute of Criminology | Their events listing pages. The Law listing also covers LCIL, CPL, 3CL, CIPIL, CELS, CFL and CEENRG. |
| Bennett School of Public Policy | Its event pages |
| Centre for Geopolitics, CGHR | Their event pages |
| King's Entrepreneurship Lab | Its events feed |
| Cambridge Judge Business School | Its events data, filtered to entrepreneurship, Accelerate Cambridge, venture capital and policy events. Admissions events and events outside Cambridge are left out. |
| Cambridge Enterprise | Its events page. Only events with a confirmed date are shown. |
| EA Cambridge | Its public Luma calendar feed |
| Eventbrite | Cambridge listings under politics, law, government, international relations, public policy, human rights, entrepreneurship, startups and venture capital. Only events located in Cambridge and on topic are kept. |
| **Termcards** (`termcards.json`) | Events you add yourself, from the termcards of the Cambridge Union, CULS, CUCA, the Labour Club, CULA, King's Politics Society and so on. These societies publish termcards as images or on Instagram, so they can't be read automatically. |

When the same event appears in several places, the app merges it into one entry. The detail panel links to every place it's listed.

## Adding termcard events

In the sidebar, open **＋ Add termcard event**, fill in the form and save. The event appears in every view straight away and is kept through refreshes. To remove one, open it and click **Delete this termcard entry**. If you don't choose a topic, it's guessed from the title, or failing that from the society: the Union and party clubs map to Politics, CULS to Law, and Leaps to Entrepreneurship.

You can also edit `termcards.json` directly:

```json
[
 {"title": "Debate: This House Would…", "organiser": "Cambridge Union", "start": "2026-11-19T19:30",
  "end": "2026-11-19T21:30", "venue": "Debating Chamber", "speaker": "", "url": "", "topics": ["Politics"]}
]
```

## Using it

- **Day**: pick a date and time to see what's on at that moment, what starts in the next 3 hours, what's later, and a timeline of the day.
- **Week**: a 7-day grid. Click a day header to open that day.
- **List**: every upcoming event, grouped by day.
- **My plan**: star events with ☆ to add them here. Clashes are flagged, and you can export the plan as an `.ics` file for Apple, Google or Outlook calendar.
- **My interests**: add keywords such as "international law", "China" or "constitutional". Matching events are highlighted, and you can choose to show only those.
- **Topics and sources**: click to turn them on or off. Alt-click (Cmd-click on a Mac) a topic to show only that one.
- **Hide**: mark events as "Not interested" to remove them from view.

Your plan, interests and filters are saved in your browser.

## Adding a source

In `scraper.py`:

- Any `*.cam.ac.uk` site built on the University's current Drupal theme can be added with one line in `DEPT_SITES`.
- To always include a talks.cam series, add its list ID (the number in `/show/index/NNNNN/`) to `TALKS_SEED_LISTS`.
