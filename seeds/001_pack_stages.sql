-- open_otto seed 001: pipeline stages for the built-in packs.
-- Required data. Safe to run once after migration 001.

INSERT INTO pipeline_stages (pack, key, label, position, is_terminal) VALUES
    ('gigs', 'prospect', 'Prospect',      1, 0),
    ('gigs', 'pitched',  'Pitched',       2, 0),
    ('gigs', 'talking',  'In discussion', 3, 0),
    ('gigs', 'booked',   'Booked',        4, 0),
    ('gigs', 'played',   'Played',        5, 0),
    ('gigs', 'rebook',   'Rebook',        6, 0),
    ('gigs', 'passed',   'Passed',        7, 1),

    ('jobs', 'saved',     'Saved',       1, 0),
    ('jobs', 'applied',   'Applied',     2, 0),
    ('jobs', 'screen',    'Screen',      3, 0),
    ('jobs', 'interview', 'Interview',   4, 0),
    ('jobs', 'offer',     'Offer',       5, 0),
    ('jobs', 'accepted',  'Accepted',    6, 1),
    ('jobs', 'rejected',  'Rejected',    7, 1),
    ('jobs', 'withdrawn', 'Withdrawn',   8, 1),

    ('freelance', 'lead',     'Lead',      1, 0),
    ('freelance', 'contacted','Contacted', 2, 0),
    ('freelance', 'quoted',   'Quoted',    3, 0),
    ('freelance', 'won',      'Won',       4, 0),
    ('freelance', 'delivered','Delivered', 5, 0),
    ('freelance', 'repeat',   'Repeat',    6, 0),
    ('freelance', 'lost',     'Lost',      7, 1);
