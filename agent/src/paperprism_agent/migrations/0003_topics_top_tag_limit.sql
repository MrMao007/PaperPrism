-- Topic display-tag cap (v0.4)
--
-- `top_tag_limit` controls how many distinct tags a topic's cards/detail
-- pages expose. The auto-tag job still writes every tag the LLM assigns
-- per paper (default 5 per paper) into paper_tags; the topic row only
-- remembers how many representative tags to surface when listing.
--
-- Default 5 matches the previous hard-coded k=5 in repository.list_topics.

ALTER TABLE topics ADD COLUMN top_tag_limit INTEGER NOT NULL DEFAULT 5;
