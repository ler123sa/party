from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
import sqlite3
import os
import time
import random

app = Flask(__name__, static_folder='static')
CORS(app)

DATABASE = os.path.join('/tmp', 'party.db') if os.path.exists('/tmp') else 'party.db'


# ─────────────────────────────────────────────────────────────────────────────
#  Database helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    """Возвращает соединение с БД привязанное к текущему запросу Flask."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, timeout=30, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA busy_timeout=30000")
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _table_columns(c, table):
    c.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in c.fetchall()}


def init_db():
    print(f"[Party] Инициализация базы данных: {DATABASE}")
    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    c = conn.cursor()

    # Старая схема использовала колонку `name` вместо `code` и таблицу `invites`.
    # Если БД создана старой версией — пересоздаём всё с нуля (старые данные не нужны).
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parties'")
    if c.fetchone() is not None:
        cols = _table_columns(c, 'parties')
        if 'code' not in cols:
            print("[Party] Обнаружена старая схема, пересоздаём таблицы...")
            c.execute('DROP TABLE IF EXISTS invites')
            c.execute('DROP TABLE IF EXISTS party_waypoints')
            c.execute('DROP TABLE IF EXISTS party_members')
            c.execute('DROP TABLE IF EXISTS parties')
            conn.commit()

    # Пати: code = 5-значный код, leader = client user (glitch.user)
    c.execute('''CREATE TABLE IF NOT EXISTS parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        leader TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Участники: player_id = client user, mc_nick = ник в Minecraft
    c.execute('''CREATE TABLE IF NOT EXISTS party_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        party_id INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        mc_nick TEXT NOT NULL DEFAULT '',
        x REAL DEFAULT 0,
        y REAL DEFAULT 0,
        z REAL DEFAULT 0,
        last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (party_id) REFERENCES parties(id) ON DELETE CASCADE,
        UNIQUE(party_id, player_id)
    )''')

    # Метки
    c.execute('''CREATE TABLE IF NOT EXISTS party_waypoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        party_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        creator TEXT NOT NULL,
        x REAL NOT NULL,
        y REAL NOT NULL,
        z REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (party_id) REFERENCES parties(id) ON DELETE CASCADE
    )''')

    conn.commit()
    conn.close()
    print("[Party] База данных готова")


def generate_unique_code(c):
    """Подбирает свободный 5-значный код."""
    for _ in range(50):
        code = f"{random.randint(0, 99999):05d}"
        c.execute('SELECT 1 FROM parties WHERE code = ?', (code,))
        if not c.fetchone():
            return code
    raise RuntimeError('Не удалось сгенерировать уникальный код')


# ─────────────────────────────────────────────────────────────────────────────
#  Party endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/party/create', methods=['POST'])
def create_party():
    data = request.json or {}
    leader = data.get('leader')
    mc_nick = data.get('mc_nick') or ''

    if not leader:
        return jsonify({'error': 'Missing leader'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        # Уже в пати?
        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (leader,))
        if c.fetchone():
            return jsonify({'error': 'Already in a party'}), 400

        code = generate_unique_code(c)
        c.execute('INSERT INTO parties (code, leader) VALUES (?, ?)', (code, leader))
        party_id = c.lastrowid

        c.execute('INSERT INTO party_members (party_id, player_id, mc_nick) VALUES (?, ?, ?)',
                  (party_id, leader, mc_nick))

        conn.commit()
        return jsonify({'success': True, 'code': code, 'message': f'Party {code} created'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/join', methods=['POST'])
def join_party():
    data = request.json or {}
    player = data.get('player')
    mc_nick = data.get('mc_nick') or ''
    code = (data.get('code') or data.get('party') or '').strip()

    if not player or not code:
        return jsonify({'error': 'Missing player or code'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        # Уже в пати?
        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (player,))
        if c.fetchone():
            return jsonify({'error': 'Already in a party'}), 400

        c.execute('SELECT id FROM parties WHERE code = ?', (code,))
        party = c.fetchone()
        if not party:
            return jsonify({'error': 'Party not found'}), 404

        c.execute('INSERT INTO party_members (party_id, player_id, mc_nick) VALUES (?, ?, ?)',
                  (party['id'], player, mc_nick))
        conn.commit()
        return jsonify({'success': True, 'code': code, 'message': f'Joined party {code}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/leave', methods=['POST'])
def leave_party():
    data = request.json or {}
    player = data.get('player')
    if not player:
        return jsonify({'error': 'Missing player'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('''SELECT pm.party_id, p.leader FROM party_members pm
                     JOIN parties p ON pm.party_id = p.id
                     WHERE pm.player_id = ?''', (player,))
        row = c.fetchone()
        if not row:
            return jsonify({'error': 'Not in a party'}), 400

        if player == row['leader']:
            return jsonify({'error': 'You are the leader. Use .party disband instead'}), 403

        c.execute('DELETE FROM party_members WHERE party_id = ? AND player_id = ?',
                  (row['party_id'], player))
        conn.commit()
        return jsonify({'success': True, 'message': 'Left party'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/disband', methods=['POST'])
def disband_party():
    data = request.json or {}
    leader = data.get('leader')
    if not leader:
        return jsonify({'error': 'Missing leader'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT id FROM parties WHERE leader = ?', (leader,))
        party = c.fetchone()
        if not party:
            return jsonify({'error': 'You are not a party leader'}), 403

        party_id = party['id']
        c.execute('DELETE FROM party_members WHERE party_id = ?', (party_id,))
        c.execute('DELETE FROM party_waypoints WHERE party_id = ?', (party_id,))
        c.execute('DELETE FROM parties WHERE id = ?', (party_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'Party disbanded'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/kick', methods=['POST'])
def kick_player():
    data = request.json or {}
    leader = data.get('leader')
    target = data.get('target')  # client user или mc_nick

    if not leader or not target:
        return jsonify({'error': 'Missing leader or target'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT id FROM parties WHERE leader = ?', (leader,))
        party = c.fetchone()
        if not party:
            return jsonify({'error': 'You are not a party leader'}), 403

        party_id = party['id']
        # Сначала пробуем по player_id, потом по mc_nick (на случай если кикают по нику)
        c.execute('DELETE FROM party_members WHERE party_id = ? AND player_id = ?',
                  (party_id, target))
        if c.rowcount == 0:
            c.execute('DELETE FROM party_members WHERE party_id = ? AND mc_nick = ?',
                      (party_id, target))

        if c.rowcount == 0:
            return jsonify({'error': 'Player not in your party'}), 400

        conn.commit()
        return jsonify({'success': True, 'message': f'Kicked {target}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/list', methods=['POST'])
def list_members():
    data = request.json or {}
    player = data.get('player')
    if not player:
        return jsonify({'error': 'Missing player'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('''SELECT p.code, pm2.player_id, pm2.mc_nick FROM party_members pm1
                     JOIN parties p ON pm1.party_id = p.id
                     JOIN party_members pm2 ON pm1.party_id = pm2.party_id
                     WHERE pm1.player_id = ?''', (player,))
        rows = c.fetchall()
        if not rows:
            return jsonify({'error': 'Not in a party'}), 400

        code = rows[0]['code']
        members = [r['mc_nick'] or r['player_id'] for r in rows]
        return jsonify({'code': code, 'members': members, 'message': ', '.join(members)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/state', methods=['POST'])
def get_party_state():
    data = request.json or {}
    player = data.get('player')
    mc_nick = data.get('mc_nick')
    if not player:
        return jsonify({'error': 'Missing player'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        if 'x' in data and 'y' in data and 'z' in data:
            if mc_nick is not None:
                c.execute('''UPDATE party_members
                             SET x = ?, y = ?, z = ?, mc_nick = ?, last_update = CURRENT_TIMESTAMP
                             WHERE player_id = ?''',
                          (data['x'], data['y'], data['z'], mc_nick, player))
            else:
                c.execute('''UPDATE party_members
                             SET x = ?, y = ?, z = ?, last_update = CURRENT_TIMESTAMP
                             WHERE player_id = ?''',
                          (data['x'], data['y'], data['z'], player))
            conn.commit()

        c.execute('''SELECT pm2.player_id, pm2.mc_nick, pm2.x, pm2.y, pm2.z
                     FROM party_members pm1
                     JOIN party_members pm2 ON pm1.party_id = pm2.party_id
                     WHERE pm1.player_id = ? AND pm2.player_id != ?''',
                  (player, player))

        members = []
        for row in c.fetchall():
            # playerId возвращаем как mc_nick — клиент использует его для матчинга с PlayerEntity
            display_id = row['mc_nick'] or row['player_id']
            members.append({
                'playerId': display_id,
                'clientUser': row['player_id'],
                'mcNick': row['mc_nick'],
                'x': row['x'],
                'y': row['y'],
                'z': row['z']
            })
        return jsonify({'members': members})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/cleanup', methods=['POST'])
def cleanup():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''DELETE FROM party_members
                     WHERE last_update < datetime('now', '-30 minutes')''')
        conn.commit()
        return jsonify({'success': True, 'message': 'Cleanup completed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Waypoints
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/party/waypoint/add', methods=['POST'])
def add_waypoint():
    data = request.json or {}
    player = data.get('player')
    name = data.get('name')
    entity_id = data.get('entityId')
    x = data.get('x'); y = data.get('y'); z = data.get('z')

    if not all([player, name, x is not None, y is not None, z is not None]):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT party_id, mc_nick FROM party_members WHERE player_id = ?', (player,))
        row = c.fetchone()
        if not row:
            return jsonify({'error': 'Not in a party'}), 400

        party_id = row['party_id']
        creator_display = row['mc_nick'] or player

        c.execute('DELETE FROM party_waypoints WHERE party_id = ? AND creator = ?',
                  (party_id, creator_display))

        waypoint_name = entity_id if entity_id else name
        c.execute('''INSERT INTO party_waypoints (party_id, name, creator, x, y, z)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (party_id, waypoint_name, creator_display, x, y, z))

        waypoint_id = c.lastrowid
        conn.commit()

        # Удалить через 20 секунд
        import threading
        def delete_after_delay():
            time.sleep(20)
            try:
                tmp = sqlite3.connect(DATABASE, timeout=30)
                tmp.execute("PRAGMA busy_timeout=30000")
                tmp.execute('DELETE FROM party_waypoints WHERE id = ?', (waypoint_id,))
                tmp.commit()
                tmp.close()
            except Exception:
                pass
        threading.Thread(target=delete_after_delay, daemon=True).start()

        return jsonify({'success': True, 'message': 'Waypoint added'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/waypoint/list', methods=['POST'])
def list_waypoints():
    data = request.json or {}
    player = data.get('player')
    if not player:
        return jsonify({'error': 'Missing player'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (player,))
        row = c.fetchone()
        if not row:
            return jsonify({'waypoints': []})

        c.execute('''SELECT name, creator, x, y, z, created_at
                     FROM party_waypoints
                     WHERE party_id = ?
                     ORDER BY created_at DESC''', (row['party_id'],))

        waypoints = []
        for r in c.fetchall():
            waypoint_name = r['name']
            entity_id = None
            try:
                import uuid
                uuid.UUID(waypoint_name)
                entity_id = waypoint_name
                waypoint_name = 'Entity'
            except Exception:
                pass
            waypoints.append({
                'entityId': entity_id,
                'name': waypoint_name,
                'creator': r['creator'],
                'x': r['x'], 'y': r['y'], 'z': r['z'],
                'timestamp': r['created_at']
            })
        return jsonify({'waypoints': waypoints})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/party/waypoint/remove', methods=['POST'])
def remove_waypoint():
    data = request.json or {}
    player = data.get('player')
    name = data.get('name')
    if not player or not name:
        return jsonify({'error': 'Missing player or name'}), 400

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT party_id, mc_nick FROM party_members WHERE player_id = ?', (player,))
        row = c.fetchone()
        if not row:
            return jsonify({'error': 'Not in a party'}), 400

        creator_display = row['mc_nick'] or player
        c.execute('''DELETE FROM party_waypoints
                     WHERE party_id = ? AND name = ? AND creator = ?''',
                  (row['party_id'], name, creator_display))
        if c.rowcount == 0:
            return jsonify({'error': 'Waypoint not found or not yours'}), 400
        conn.commit()
        return jsonify({'success': True, 'message': f'Waypoint {name} removed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Misc
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return jsonify({
        'name': 'Party API Server',
        'version': '2.0.0',
        'status': 'running',
        'endpoints': [
            'POST /party/create',
            'POST /party/join',
            'POST /party/leave',
            'POST /party/disband',
            'POST /party/kick',
            'POST /party/list',
            'POST /party/state',
            'POST /party/waypoint/add',
            'POST /party/waypoint/list',
            'POST /party/waypoint/remove'
        ]
    })


@app.route('/dashboard')
def dashboard():
    return send_from_directory('static', 'index.html')


@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM parties')
        total_parties = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM party_members')
        total_players = c.fetchone()[0]

        c.execute('''SELECT p.id, p.code, p.leader, p.created_at,
                     GROUP_CONCAT(COALESCE(NULLIF(pm.mc_nick, ''), pm.player_id)) as members
                     FROM parties p
                     LEFT JOIN party_members pm ON p.id = pm.party_id
                     GROUP BY p.id''')
        parties = []
        for row in c.fetchall():
            members = row[4].split(',') if row[4] else []
            parties.append({
                'id': row[0],
                'code': row[1],
                'leader': row[2],
                'created_at': row[3],
                'members': members,
                'member_count': len(members)
            })

        return jsonify({
            'total_parties': total_parties,
            'total_players': total_players,
            'total_invites': 0,
            'parties': parties
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


print("[Party] Запуск Party API Server v2.0...")
print(f"[Party] Путь к базе данных: {DATABASE}")
init_db()
print("[Party] Сервер готов к работе!")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
