import sqlite3
import os

db_path = 'backend/data/arena.db'

if not os.path.exists(db_path):
    print(f"DB not found: {db_path}")
else:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    print('Problems count:', c.execute('SELECT COUNT(*) FROM problems').fetchone()[0])
    print('First problem:')
    c.execute('SELECT id, name, slug, test_count, tests_downloaded, length(description_md) as desc_len, length(sample_input) as sample_len FROM problems ORDER BY id LIMIT 1')
    print(c.fetchone())
    print('All problems summary:')
    for row in c.execute('SELECT id, name, test_count, tests_downloaded FROM problems'):
        print(row)
    # Check description content for first
    print('First description preview:')
    c.execute('SELECT substr(description_md, 1, 500) FROM problems ORDER BY id LIMIT 1')
    desc = c.fetchone()
    if desc:
        print(repr(desc[0][:200]) + '...' if len(desc[0]) > 200 else repr(desc[0]))
    conn.close()