-- open_otto demo data: a fictional jazz duo booking venues.
-- Every venue, person, and address here is invented.
-- Dates are relative to now, so follow-up views always have something to show.
-- Requires seeds/001_pack_stages.sql.

INSERT INTO orgs (pack, name, kind, website, city, region, source, notes, stage_id, created_at) VALUES
  ('gigs', 'The Blue Heron Room',    'jazz club',     'https://example.com/blue-heron',  'Riverton',  'MA', 'manual',
   'Listening room, 60 seats. Thursday slots open. Pays door split.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='talking'),  datetime('now','-40 days')),
  ('gigs', 'Copper Kettle Cafe',     'cafe',          'https://example.com/copper-kettle','Riverton',  'MA', 'csv',
   'Sunday brunch music, 11am-1pm. Flat fee, small PA provided.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='pitched'),  datetime('now','-21 days')),
  ('gigs', 'Lantern Hill Winery',    'winery',        'https://example.com/lantern-hill', 'Eastford',  'CT', 'referral',
   'Patio series, May through September. Books the prior winter.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='pitched'),  datetime('now','-30 days')),
  ('gigs', 'Harborlight Bistro',     'restaurant',    'https://example.com/harborlight',  'Port Mara', 'MA', 'manual',
   'Plays Friday dinner sets. Owner prefers text over email.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='booked'),   datetime('now','-60 days')),
  ('gigs', 'Maple Street Taproom',   'brewery',       'https://example.com/maple-taproom','Eastford',  'CT', 'csv',
   'Open mic Tuesdays. Featured-act nights monthly.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='prospect'), datetime('now','-5 days')),
  ('gigs', 'Old Mill Arts Center',   'arts center',   'https://example.com/old-mill',     'Riverton',  'MA', 'manual',
   'Concert series, books 6 months out. Grant-funded, pays well.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='prospect'), datetime('now','-12 days')),
  ('gigs', 'Fernwood Hotel Lounge',  'hotel lounge',  'https://example.com/fernwood',     'Port Mara', 'MA', 'referral',
   'Played here last fall. Went well. Ask about spring.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='rebook'),   datetime('now','-90 days')),
  ('gigs', 'Pinecrest Golf Club',    'private club',  NULL,                               'Eastford',  'CT', 'manual',
   'Said they only use an agency. Not a fit.',
   (SELECT id FROM pipeline_stages WHERE pack='gigs' AND key='passed'),   datetime('now','-75 days'));

INSERT INTO contacts (org_id, name, role, email, phone, notes, is_primary)
SELECT o.id, c.name, c.role, c.email, c.phone, c.notes, c.is_primary
FROM (
  SELECT 'The Blue Heron Room'   AS org, 'Dana Whitfield' AS name, 'Talent buyer' AS role, 'dana@example.com'   AS email, NULL AS phone, 'Replies within a day or two.' AS notes, 1 AS is_primary
  UNION ALL SELECT 'Copper Kettle Cafe',    'Marcus Oyelaran', 'Owner',          'marcus@example.com', NULL,            NULL,                         1
  UNION ALL SELECT 'Lantern Hill Winery',   'Priya Ramanathan','Events manager', 'events@example.com', NULL,            'Referred by a mutual friend.', 1
  UNION ALL SELECT 'Harborlight Bistro',    'Tomasz Kowal',    'Owner',          NULL,                 '555-0142',      'Text only.',                 1
  UNION ALL SELECT 'Maple Street Taproom',  'Jess Albrecht',   'Manager',        'jess@example.com',   NULL,            NULL,                         1
  UNION ALL SELECT 'Old Mill Arts Center',  'Helen Park',      'Program director','helen@example.com', NULL,            'Wants a one-page press kit.', 1
  UNION ALL SELECT 'Fernwood Hotel Lounge', 'Greg Sandoval',   'F&B manager',    'greg@example.com',   NULL,            NULL,                         1
) c
JOIN orgs o ON o.pack = 'gigs' AND o.name = c.org;

-- History. Outbound/inbound mix chosen so the follow-up view has real answers:
--   Copper Kettle: we pitched, no reply, 14 days silent  -> has a pending draft, so NOT a candidate
--   Lantern Hill:  we pitched, no reply, 22 days silent  -> the one follow-up candidate
--   Blue Heron:    they replied last, so ball is in our court, not a "silent" case
--   Harborlight:   booked, recent activity
INSERT INTO interactions (org_id, contact_id, channel, direction, summary, occurred_at)
SELECT o.id, (SELECT id FROM contacts WHERE org_id = o.id AND is_primary = 1), h.channel, h.direction, h.summary, datetime('now', h.offset)
FROM (
  SELECT 'The Blue Heron Room' AS org, 'email' AS channel, 'outbound' AS direction, 'Intro email with live video link.' AS summary, '-38 days' AS offset
  UNION ALL SELECT 'The Blue Heron Room', 'email', 'inbound',  'Dana liked the video. Asked for two Thursday dates and rates.', '-34 days'
  UNION ALL SELECT 'The Blue Heron Room', 'email', 'outbound', 'Sent two dates and rate card.',                                    '-33 days'
  UNION ALL SELECT 'The Blue Heron Room', 'email', 'inbound',  'Dana is checking the calendar, will confirm shortly.',             '-6 days'
  UNION ALL SELECT 'Copper Kettle Cafe',  'email', 'outbound', 'Pitched Sunday brunch duo set.',                                   '-14 days'
  UNION ALL SELECT 'Lantern Hill Winery', 'email', 'outbound', 'Pitched summer patio series.',                                     '-22 days'
  UNION ALL SELECT 'Harborlight Bistro',  'text',  'outbound', 'Asked about Friday dinner sets.',                                  '-55 days'
  UNION ALL SELECT 'Harborlight Bistro',  'text',  'inbound',  'Tomasz said yes, first Friday of each month.',                     '-54 days'
  UNION ALL SELECT 'Harborlight Bistro',  'in_person','inbound','Played first date. Tomasz happy, wants to keep going.',            '-8 days'
  UNION ALL SELECT 'Fernwood Hotel Lounge','in_person','inbound','Played last fall. Greg said to check back in spring.',           '-85 days'
) h
JOIN orgs o ON o.pack = 'gigs' AND o.name = h.org;

-- Open tasks.
INSERT INTO tasks (org_id, title, due_at, created_by)
SELECT o.id, t.title, datetime('now', t.offset), t.by
FROM (
  SELECT 'The Blue Heron Room' AS org, 'Nudge Dana if no confirmation by Friday' AS title, '+3 days' AS offset, 'user' AS by
  UNION ALL SELECT 'Old Mill Arts Center', 'Build one-page press kit for Helen Park',        '+5 days',  'user'
  UNION ALL SELECT 'Fernwood Hotel Lounge','Ask Greg about spring dates',                    '-2 days',  'scheduler'
) t
JOIN orgs o ON o.pack = 'gigs' AND o.name = t.org;

-- One pending agent draft waiting on a human decision.
INSERT INTO drafts (org_id, contact_id, channel, subject, body, created_by, model)
SELECT o.id, c.id, 'email', 'Quick follow-up on Sunday brunch music',
  'Hi Marcus, I wanted to follow up on my note from two weeks ago about Sunday brunch sets at Copper Kettle. '
  || 'We are a guitar and vocal duo playing standards at conversational volume. Happy to send a short video or '
  || 'come play a trial Sunday if that is easier. Would a quick call this week work?',
  'agent', 'demo-model'
FROM orgs o JOIN contacts c ON c.org_id = o.id AND c.is_primary = 1
WHERE o.pack = 'gigs' AND o.name = 'Copper Kettle Cafe';

-- Seed audit trail entry so the log is not empty on first look.
INSERT INTO audit_log (actor, action, entity_type, entity_id, detail)
VALUES ('system', 'demo_seed_loaded', 'system', NULL, json_object('pack', 'gigs'));
