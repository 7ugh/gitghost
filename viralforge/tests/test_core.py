import json, os, pathlib, unittest
from unittest import mock
from viralforge import research, ideas, factcheck, scriptgen, util, analytics, db, config, describe

FIX = pathlib.Path(__file__).parent.parent / "viralforge" / "fixtures"


class Parsers(unittest.TestCase):
    def test_google_trends_rss(self):
        xml = b'''<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>solar eclipse</title><ht:approx_traffic>200,000+</ht:approx_traffic>
        <ht:news_item><ht:news_item_title>Eclipse path explained</ht:news_item_title></ht:news_item></item></channel></rss>'''
        with mock.patch.object(util, "fetch", return_value=xml):
            r = research.src_google_trends({"geo": "US"}, {})
        self.assertEqual(r[0]["title"], "solar eclipse"); self.assertIn("Eclipse path", r[0]["summary"]); self.assertEqual(r[0]["score"], 2000.0)

    def test_reddit_filters_nsfw_and_stickied(self):
        post = lambda t, **k: {"data": {"title": t, "permalink": "/r/x/1", "ups": 10, "num_comments": 5, "created_utc": 0, "over_18": False, "stickied": False, **k}}
        j = {"data": {"children": [post("ok"), post("nsfw", over_18=True), post("sticky", stickied=True)]}}
        with mock.patch.object(util, "fetch_json", return_value=j), mock.patch("time.sleep"):
            r = research.src_reddit({"subreddits": ["todayilearned"]}, {})
        self.assertEqual([x["title"] for x in r], ["ok"]); self.assertEqual(r[0]["category"], "facts")

    def test_rss_news(self):
        xml = b"<rss><channel><item><title>Robot learns to juggle</title><link>http://a/b</link><description>&lt;p&gt;Scientists&lt;/p&gt; report</description></item></channel></rss>"
        with mock.patch.object(util, "fetch", return_value=xml):
            r = research.src_news_rss({"feeds": ["http://feeds.example.com/x.xml"]}, {})
        self.assertEqual(r[0]["summary"], "Scientists report")

    def test_all_sources_failing_is_reported_not_faked(self):
        s = config.load()
        with mock.patch.object(util, "fetch", side_effect=OSError("blocked")), mock.patch.object(util, "fetch_json", side_effect=OSError("blocked")), mock.patch("time.sleep"):
            res = research.run_research(s)
        self.assertEqual(res["count"], 0)
        self.assertFalse(res["status"]["reddit"]["ok"]); self.assertIn("blocked", res["status"]["reddit"]["message"])

    def test_fixture_is_labelled_sample(self):
        res = research.run_research(fixture=str(FIX / "sample_signals.json"))
        self.assertIn("SAMPLE", res["status"]["fixture"]["message"])


class Ideas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research.run_research(fixture=str(FIX / "sample_signals.json"))
        cls.ranked = ideas.generate()

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(ideas.WEIGHTS.values()), 1.0)

    def test_sensitive_excluded_and_listed(self):
        self.assertFalse(any("shooting" in i["topic"].lower() for i in self.ranked))
        self.assertTrue(any("shooting" in t.lower() for t in self.ranked[0]["meta"]["excluded_sensitive"]))

    def test_clusters_related_signals(self):
        top = self.ranked[0]
        self.assertIn("octopus", top["topic"].lower()); self.assertGreaterEqual(len(top["signals"]), 3)

    def test_explanations_and_no_guarantees(self):
        for i in self.ranked:
            self.assertEqual(set(i["breakdown"]), set(ideas.WEIGHTS)); self.assertIn("not 'will get views'", i["why"])
            self.assertTrue(0 <= i["score"] <= 100)
        self.assertEqual([i["score"] for i in self.ranked], sorted((i["score"] for i in self.ranked), reverse=True))

    def test_pick_skips_recent_duplicates(self):
        p = ideas.pick(self.ranked, [self.ranked[0]["topic"]])
        self.assertNotEqual(p["topic"], self.ranked[0]["topic"])


class FactAndScript(unittest.TestCase):
    notes = json.load(open(FIX / "octopus_notes.json"))

    def test_supported_vs_fabricated(self):
        ok = factcheck.check_claim("An octopus has three hearts", self.notes["facts"])
        bad = factcheck.check_claim("An octopus has seven hearts", self.notes["facts"])
        self.assertEqual(ok["status"], "supported"); self.assertNotEqual(bad["status"], "supported")

    def test_unrelated_claim_unsupported(self):
        self.assertEqual(factcheck.check_claim("The moon is made of cheese", self.notes["facts"])["status"], "unsupported")

    def test_template_script_and_traceability(self):
        sc = scriptgen.template_script({"topic": "TIL octopuses have three hearts"}, self.notes)
        fc = factcheck.check_script(sc, self.notes)
        self.assertTrue(fc["ok"]); self.assertEqual(fc["counts"]["unsupported"], 0)
        self.assertEqual(sc["scenes"][1]["stat"], "3"); self.assertNotIn("stat", sc["scenes"][4])   # no misleading "one reason" => 1

    def test_template_refuses_fiction_styles(self):
        with self.assertRaises(RuntimeError):
            scriptgen.template_script({"topic": "x"}, self.notes, "story")

    def test_no_sentence_truncated_mid_clause(self):
        long = "word " * 40 + "end"
        self.assertTrue(scriptgen._shorten(long).endswith("end"))

    def test_description_limits(self):
        sc = scriptgen.template_script({"topic": "octopus"}, self.notes)
        d = describe.build(sc, "octopus", "facts", self.notes["sources"])
        self.assertLessEqual(d["length"], 2200); self.assertLessEqual(len(d["hashtags"]), 30); self.assertIn("AI-generated", d["caption"])


class Analytics(unittest.TestCase):
    def _mk(self, vid, cat, style, shares, saves, comments, likes, reach):
        db.x("INSERT OR REPLACE INTO videos(id,topic,category,style,status,title,created_at,updated_at,posted_at,duration,meta,qc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (vid, vid, cat, style, "posted", vid, 1, 1, 1_700_000_000 + hash(vid) % 1000, 30.0, "{}", "{}"))
        analytics.record(vid, "import", {"reach": reach, "shares": shares, "saves": saves, "comments": comments, "likes": likes, "avg_watch_s": 15})

    def test_engagement_formula(self):
        self.assertEqual(analytics.engagement({"reach": 1000, "shares": 10, "saves": 10, "comments": 10, "likes": 100}), 18.0)
        self.assertIsNone(analytics.engagement({"likes": 5}))

    def test_learn_requires_data_and_shrinks(self):
        self.assertIn("need", analytics.learn().get("note", "need"))      # nothing yet (or too few)
        for i in range(4): self._mk(f"a{i}", "facts", "explainer", 40, 40, 20, 200, 1000)
        for i in range(3): self._mk(f"b{i}", "comedy", "comedy", 2, 2, 2, 20, 1000)
        L = analytics.learn()
        self.assertGreater(L["category"]["facts"], 1.0); self.assertLess(L["category"]["comedy"], 1.0)
        self.assertTrue(all(0.85 <= v <= 1.20 for v in L["category"].values())); self.assertEqual(L["confidence"], "low")

    def test_csv_import(self):
        self._mk("csvvid", "facts", "explainer", 1, 1, 1, 1, 100)
        r = analytics.import_csv("id,views,reach,shares,completion_rate\ncsvvid,500,400,20,55\nnope,1,1,1,1")
        self.assertEqual(r["imported"], 1); self.assertEqual(r["unmatched"], ["nope"])
        self.assertAlmostEqual(analytics.latest("csvvid")["completion_rate"], 0.55)

    def test_recommend_time_is_future(self):
        import time
        r = analytics.recommend_time(config.load(), now=time.time())
        self.assertGreater(r["at"], time.time())


if __name__ == "__main__":
    unittest.main()
