# osu!web Matchmaking 系统分析

## 概述

本文档分析了 osu!web 项目中的 matchmaking（匹配）系统的数据库表结构和相关代码实现。该系统基于 Laravel 框架开发，支持基于 ELO 评级的技能匹配功能。

## 数据库表结构

### 1. `matchmaking_user_stats` - 用户匹配统计表

存储每个用户在不同游戏模式下的匹配统计信息。

```sql
CREATE TABLE matchmaking_user_stats (
    user_id INT UNSIGNED NOT NULL,              -- 用户ID
    ruleset_id SMALLINT NOT NULL,               -- 游戏模式ID (0:osu!, 1:taiko, 2:catch, 3:mania)
    first_placements INT UNSIGNED DEFAULT 0,    -- 首次定级赛次数
    total_points INT UNSIGNED DEFAULT 0,        -- 总积分
    elo_data JSON NULL,                         -- ELO评级数据 (JSON格式)
    created_at TIMESTAMP NULL,                  -- 创建时间
    updated_at TIMESTAMP NULL,                  -- 更新时间

    PRIMARY KEY (user_id, ruleset_id)           -- 复合主键
);
```

**字段说明：**
- `user_id`: 关联用户表的用户ID
- `ruleset_id`: 游戏模式标识符 (0=osu!, 1=taiko, 2=catch, 3=mania)
- `first_placements`: 记录用户首次定级赛的次数
- `total_points`: 用户在该模式下的总积分
- `elo_data`: JSON格式存储的ELO评级相关数据

### 2. `matchmaking_pools` - 匹配池表

定义可用的匹配池配置。

```sql
CREATE TABLE matchmaking_pools (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, -- 自增主键
    ruleset_id SMALLINT NOT NULL,               -- 游戏模式ID
    variant_id SMALLINT DEFAULT 0,              -- 变体ID
    name VARCHAR(255) NOT NULL,                 -- 匹配池名称
    active BOOLEAN NOT NULL,                    -- 是否激活
    created_at TIMESTAMP NULL,                  -- 创建时间
    updated_at TIMESTAMP NULL,                  -- 更新时间

    INDEX idx_ruleset_variant_active (ruleset_id, variant_id, active)
);
```

**字段说明：**
- `ruleset_id`: 对应的游戏模式
- `variant_id`: 支持同一游戏模式的不同变体
- `name`: 匹配池的名称
- `active`: 标识该匹配池是否当前可用

### 3. `matchmaking_pool_beatmaps` - 匹配池谱面关联表

定义每个匹配池包含的谱面。

```sql
CREATE TABLE matchmaking_pool_beatmaps (
    pool_id INT UNSIGNED NOT NULL,              -- 匹配池ID
    beatmap_id MEDIUMINT UNSIGNED NOT NULL,     -- 谱面ID
    mods JSON NULL,                            -- MOD配置 (JSON格式)

    INDEX idx_pool_id (pool_id)
);
```

**字段说明：**
- `pool_id`: 关联匹配池表的ID
- `beatmap_id`: 关联谱面表的ID
- `mods`: JSON格式存储的MOD配置信息

### 4. `multiplayer_rooms` - 多人房间表 (扩展)

原有的多人房间表新增了matchmaking类型支持。

```sql
-- 在原有的 type 枚举中新增 'matchmaking'
ALTER TABLE multiplayer_rooms MODIFY type ENUM(
    'playlists',
    'head_to_head',
    'team_versus',
    'matchmaking'        -- 新增的匹配模式
) NOT NULL;
```

## 数据表关系

```
User (用户)
    ↓
matchmaking_user_stats (用户统计)
    ↓
matchmaking_pools (匹配池) ← ruleset_id → Ruleset (游戏模式)
    ↓
matchmaking_pool_beatmaps (池内谱面)
    ↓
Beatmap (谱面)
    ↓
multiplayer_rooms (type='matchmaking') (匹配房间)
```

## 核心代码实现

### Room 模型中的匹配支持

**文件位置：** `app/Models/Multiplayer/Room.php`

#### 常量定义

```php
const MATCHMAKING_TYPE = 'matchmaking';
const REALTIME_TYPES = [...self::REALTIME_STANDARD_TYPES, self::MATCHMAKING_TYPE];
```

#### 匹配房间判断

```php
public function isMatchmaking()
{
    return $this->type === static::MATCHMAKING_TYPE;
}
```

#### 匹配房间创建逻辑

```php
// 匹配模式下，所有播放列表项由BanchoBot拥有
if ($this->isMatchmaking()) {
    $banchoBotId = $GLOBALS['cfg']['osu']['legacy']['bancho_bot_user_id'];
    foreach ($playlistItems as $item) {
        $item->owner_id = $banchoBotId;
    }
}
```

#### 权限控制

```php
// 普通用户无法创建匹配房间
if ($this->isMatchmaking()) {
    throw new InvariantException('matchmaking rooms cannot be created');
}
```

### 控制器层

#### 多人游戏控制器

**文件位置：** `app/Http/Controllers/Multiplayer/RoomsController.php`

- 处理房间的展示、事件获取等功能
- 支持实时房间事件的处理
- 包含房间销毁功能

#### InterOp 控制器

**文件位置：** `app/Http/Controllers/InterOp/Multiplayer/RoomsController.php`

```php
public function store()
{
    $params = \Request::all();
    $user = User::findOrFail(get_int($params['user_id'] ?? null));

    $room = (new Room())->startGame($user, $params);

    return $room->getKey();
}
```

处理游戏服务器与web服务器之间的通信，包括：
- 房间创建
- 用户加入/离开房间

### 路由配置

**文件位置：** `routes/web.php`

```php
// 多人游戏相关路由
Route::group(['prefix' => 'multiplayer', 'as' => 'multiplayer.', 'namespace' => 'Multiplayer'], function () {
    Route::get('rooms/{room}/events', 'RoomsController@events')->name('rooms.events');
    Route::resource('rooms', 'RoomsController', ['only' => ['show']]);
});

// API路由
Route::group(['as' => 'multiplayer.', 'namespace' => 'Multiplayer', 'prefix' => 'multiplayer'], function () {
    Route::put('rooms/{room}/users/{user}', 'RoomsController@join')->name('rooms.join');
    Route::delete('rooms/{room}/users/{user}', 'RoomsController@part')->name('rooms.part');
    Route::apiResource('rooms', 'RoomsController', ['only' => ['store']]);
});
```

## 数据库迁移历史

### 迁移文件时间线

1. **2025-09-04**: 初始匹配系统创建
   - `add_matchmaking_room_type.php` - 添加房间类型
   - `create_matchmaking_user_stats_table.php` - 创建用户统计表
   - `create_matchmaking_pools_table.php` - 创建匹配池表
   - `create_matchmaking_pool_beatmaps_table.php` - 创建池谱面关联表
   - `adjust_matchmaking_tables.php` - 调整表结构

2. **2025-09-05**:
   - `remove_matchmaking_pool_beatmaps_pk.php` - 移除主键约束

3. **2025-09-08**:
   - `add_matchmaking_elo_columns.php` - 添加ELO评级列

## 系统特点

### 1. ELO评级系统
- 使用JSON格式存储复杂的评级数据
- 支持多维度的技能评估
- 可扩展的评级算法

### 2. 多模式支持
- 支持osu!的四种游戏模式
- 每个模式独立的统计和评级
- 支持模式变体扩展

### 3. 灵活的匹配池
- 动态配置的谱面池
- 支持MOD配置
- 可启用/禁用的匹配池

### 4. 系统级控制
- 匹配房间只能由系统创建
- BanchoBot拥有所有匹配房间的播放列表
- 与现有多人游戏系统无缝集成

### 5. 实时性支持
- 支持实时房间事件
- 用户加入/离开的即时处理
- 与游戏客户端的双向通信

## 使用场景

1. **技能匹配**: 根据ELO评级匹配相似技能水平的玩家
2. **模式特化**: 不同游戏模式的独立匹配
3. **谱面池管理**: 动态调整可用谱面
4. **竞技环境**: 提供公平的竞技匹配环境

## 技术栈

- **后端框架**: Laravel (PHP)
- **数据库**: MySQL
- **实时通信**: WebSocket/长轮询
- **JSON存储**: 灵活的数据结构
- **索引优化**: 针对查询性能的索引设计

---

*本文档基于osu-web项目的源代码分析生成，版本日期：2025年9月*
