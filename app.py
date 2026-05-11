from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import sqlite3
import json
import os

app = Flask(__name__, static_folder='static')
CORS(app)  # Разрешаем запросы с клиента

DATABASE = 'party.db'

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Таблица пати
    c.execute('''CREATE TABLE IF NOT EXISTS parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        leader TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Таблица участников
    c.execute('''CREATE TABLE IF NOT EXISTS party_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        party_id INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        x REAL DEFAULT 0,
        y REAL DEFAULT 0,
        z REAL DEFAULT 0,
        last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (party_id) REFERENCES parties(id) ON DELETE CASCADE,
        UNIQUE(party_id, player_id)
    )''')
    
    # Таблица приглашений
    c.execute('''CREATE TABLE IF NOT EXISTS invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        party_id INTEGER NOT NULL,
        target_player TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (party_id) REFERENCES parties(id) ON DELETE CASCADE,
        UNIQUE(party_id, target_player)
    )''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Создать пати
@app.route('/party/create', methods=['POST'])
def create_party():
    data = request.json
    leader = data.get('leader')
    name = data.get('name')
    
    if not leader or not name:
        return jsonify({'error': 'Missing leader or name'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, не состоит ли игрок уже в пати
        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (leader,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Already in a party'}), 400
        
        # Создаем пати
        c.execute('INSERT INTO parties (name, leader) VALUES (?, ?)', (name, leader))
        party_id = c.lastrowid
        
        # Добавляем лидера как участника
        c.execute('INSERT INTO party_members (party_id, player_id) VALUES (?, ?)', (party_id, leader))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Party {name} created'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Party name already exists'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Пригласить игрока
@app.route('/party/invite', methods=['POST'])
def invite_player():
    data = request.json
    leader = data.get('leader')
    target = data.get('target')
    
    if not leader or not target:
        return jsonify({'error': 'Missing leader or target'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, что игрок - лидер пати
        c.execute('SELECT id, name FROM parties WHERE leader = ?', (leader,))
        party = c.fetchone()
        
        if not party:
            conn.close()
            return jsonify({'error': 'You are not a party leader'}), 403
        
        party_id = party['id']
        
        # Проверяем, не в пати ли уже целевой игрок
        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (target,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Player already in a party'}), 400
        
        # Создаем приглашение
        c.execute('INSERT OR IGNORE INTO invites (party_id, target_player) VALUES (?, ?)', 
                  (party_id, target))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Invited {target}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Получить приглашения
@app.route('/party/invites', methods=['POST'])
def get_invites():
    data = request.json
    player = data.get('player')
    
    if not player:
        return jsonify({'error': 'Missing player'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Получаем все приглашения для игрока
        c.execute('''SELECT p.name FROM invites i 
                     JOIN parties p ON i.party_id = p.id 
                     WHERE i.target_player = ?''', (player,))
        
        invites = [row['name'] for row in c.fetchall()]
        conn.close()
        
        return jsonify({'invites': invites})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Войти в пати
@app.route('/party/join', methods=['POST'])
def join_party():
    data = request.json
    player = data.get('player')
    party_name = data.get('party')
    
    if not player or not party_name:
        return jsonify({'error': 'Missing player or party'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, не в пати ли уже игрок
        c.execute('SELECT party_id FROM party_members WHERE player_id = ?', (player,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Already in a party'}), 400
        
        # Находим пати
        c.execute('SELECT id FROM parties WHERE name = ?', (party_name,))
        party = c.fetchone()
        
        if not party:
            conn.close()
            return jsonify({'error': 'Party not found'}), 404
        
        party_id = party['id']
        
        # Проверяем приглашение
        c.execute('SELECT id FROM invites WHERE party_id = ? AND target_player = ?', 
                  (party_id, player))
        invite = c.fetchone()
        
        if not invite:
            conn.close()
            return jsonify({'error': 'No invite found'}), 403
        
        # Добавляем игрока в пати
        c.execute('INSERT INTO party_members (party_id, player_id) VALUES (?, ?)', 
                  (party_id, player))
        
        # Удаляем приглашение
        c.execute('DELETE FROM invites WHERE id = ?', (invite['id'],))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Joined party {party_name}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Выйти из пати
@app.route('/party/leave', methods=['POST'])
def leave_party():
    data = request.json
    player = data.get('player')
    
    if not player:
        return jsonify({'error': 'Missing player'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Находим пати игрока
        c.execute('''SELECT pm.party_id, p.leader FROM party_members pm
                     JOIN parties p ON pm.party_id = p.id
                     WHERE pm.player_id = ?''', (player,))
        result = c.fetchone()
        
        if not result:
            conn.close()
            return jsonify({'error': 'Not in a party'}), 400
        
        party_id = result['party_id']
        leader = result['leader']
        
        # Если игрок - лидер, распускаем пати
        if player == leader:
            c.execute('DELETE FROM parties WHERE id = ?', (party_id,))
        else:
            # Просто удаляем игрока
            c.execute('DELETE FROM party_members WHERE party_id = ? AND player_id = ?', 
                      (party_id, player))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Left party'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Распустить пати
@app.route('/party/disband', methods=['POST'])
def disband_party():
    data = request.json
    leader = data.get('leader')
    
    if not leader:
        return jsonify({'error': 'Missing leader'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, что игрок - лидер
        c.execute('SELECT id FROM parties WHERE leader = ?', (leader,))
        party = c.fetchone()
        
        if not party:
            conn.close()
            return jsonify({'error': 'You are not a party leader'}), 403
        
        # Удаляем пати (каскадно удалятся участники и приглашения)
        c.execute('DELETE FROM parties WHERE id = ?', (party['id'],))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Party disbanded'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Кикнуть игрока
@app.route('/party/kick', methods=['POST'])
def kick_player():
    data = request.json
    leader = data.get('leader')
    target = data.get('target')
    
    if not leader or not target:
        return jsonify({'error': 'Missing leader or target'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, что игрок - лидер
        c.execute('SELECT id FROM parties WHERE leader = ?', (leader,))
        party = c.fetchone()
        
        if not party:
            conn.close()
            return jsonify({'error': 'You are not a party leader'}), 403
        
        party_id = party['id']
        
        # Удаляем игрока из пати
        c.execute('DELETE FROM party_members WHERE party_id = ? AND player_id = ?', 
                  (party_id, target))
        
        if c.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Player not in your party'}), 400
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Kicked {target}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Список участников
@app.route('/party/list', methods=['POST'])
def list_members():
    data = request.json
    player = data.get('player')
    
    if not player:
        return jsonify({'error': 'Missing player'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Находим пати игрока
        c.execute('''SELECT pm2.player_id FROM party_members pm1
                     JOIN party_members pm2 ON pm1.party_id = pm2.party_id
                     WHERE pm1.player_id = ?''', (player,))
        
        members = [row['player_id'] for row in c.fetchall()]
        conn.close()
        
        if not members:
            return jsonify({'error': 'Not in a party'}), 400
        
        return jsonify({'message': ', '.join(members)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Получить состояние пати (координаты всех участников)
@app.route('/party/state', methods=['POST'])
def get_party_state():
    data = request.json
    player = data.get('player')
    
    if not player:
        return jsonify({'error': 'Missing player'}), 400
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Обновляем координаты игрока, если они переданы
        if 'x' in data and 'y' in data and 'z' in data:
            c.execute('''UPDATE party_members 
                         SET x = ?, y = ?, z = ?, last_update = CURRENT_TIMESTAMP
                         WHERE player_id = ?''', 
                      (data['x'], data['y'], data['z'], player))
            conn.commit()
        
        # Получаем координаты всех участников пати
        c.execute('''SELECT pm2.player_id, pm2.x, pm2.y, pm2.z 
                     FROM party_members pm1
                     JOIN party_members pm2 ON pm1.party_id = pm2.party_id
                     WHERE pm1.player_id = ? AND pm2.player_id != ?''', 
                  (player, player))
        
        members = []
        for row in c.fetchall():
            members.append({
                'playerId': row['player_id'],
                'x': row['x'],
                'y': row['y'],
                'z': row['z']
            })
        
        conn.close()
        
        return jsonify({'members': members})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Очистка старых данных (опционально, можно запускать по крону)
@app.route('/party/cleanup', methods=['POST'])
def cleanup():
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Удаляем старые приглашения (старше 24 часов)
        c.execute('''DELETE FROM invites 
                     WHERE created_at < datetime('now', '-1 day')''')
        
        # Удаляем неактивных игроков (не обновляли координаты 30 минут)
        c.execute('''DELETE FROM party_members 
                     WHERE last_update < datetime('now', '-30 minutes')''')
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Cleanup completed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Главная страница (для проверки)
@app.route('/')
def index():
    return jsonify({
        'name': 'Party API Server',
        'version': '1.0.0',
        'status': 'running',
        'endpoints': [
            'POST /party/create',
            'POST /party/invite',
            'POST /party/invites',
            'POST /party/join',
            'POST /party/leave',
            'POST /party/disband',
            'POST /party/kick',
            'POST /party/list',
            'POST /party/state'
        ]
    })

# Веб-интерфейс
@app.route('/dashboard')
def dashboard():
    return send_from_directory('static', 'index.html')

# Статистика для дашборда
@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Количество пати
        c.execute('SELECT COUNT(*) FROM parties')
        total_parties = c.fetchone()[0]
        
        # Количество игроков
        c.execute('SELECT COUNT(*) FROM party_members')
        total_players = c.fetchone()[0]
        
        # Количество приглашений
        c.execute('SELECT COUNT(*) FROM invites')
        total_invites = c.fetchone()[0]
        
        # Список пати с участниками
        c.execute('''SELECT p.id, p.name, p.leader, p.created_at,
                     GROUP_CONCAT(pm.player_id) as members
                     FROM parties p
                     LEFT JOIN party_members pm ON p.id = pm.party_id
                     GROUP BY p.id''')
        
        parties = []
        for row in c.fetchall():
            members = row[4].split(',') if row[4] else []
            parties.append({
                'id': row[0],
                'name': row[1],
                'leader': row[2],
                'created_at': row[3],
                'members': members,
                'member_count': len(members)
            })
        
        conn.close()
        
        return jsonify({
            'total_parties': total_parties,
            'total_players': total_players,
            'total_invites': total_invites,
            'parties': parties
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    init_db()
    # Для разработки
    app.run(host='0.0.0.0', port=5000, debug=True)
    
    # Для продакшена используйте gunicorn:
    # gunicorn -w 4 -b 0.0.0.0:5000 app:app
