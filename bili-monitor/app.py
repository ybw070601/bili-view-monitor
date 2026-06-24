# app.py
# -*- coding: utf-8 -*-
"""
B站视频播放量监控 Web 应用
主入口文件
"""

import os
import re
import time
import json
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import requests
from bs4 import BeautifulSoup

# 初始化 Flask 应用
app = Flask(__name__)
app.secret_key = os.urandom(24)

# 数据库配置
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'bilibili_monitor.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False

db = SQLAlchemy(app)


# ==================== 数据库模型 ====================

class Video(db.Model):
    """视频信息表"""
    __tablename__ = 'videos'

    id = db.Column(db.Integer, primary_key=True)
    aid = db.Column(db.String(20), unique=True, nullable=False)  # AV号
    bvid = db.Column(db.String(20))  # BV号
    title = db.Column(db.String(500))
    author = db.Column(db.String(200))
    author_id = db.Column(db.String(20))
    url = db.Column(db.String(500))
    duration = db.Column(db.Integer)  # 总时长（秒）
    desc = db.Column(db.Text)
    tags = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联每日统计
    daily_stats = db.relationship('DailyStat', backref='video', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'aid': self.aid,
            'bvid': self.bvid,
            'title': self.title,
            'author': self.author,
            'author_id': self.author_id,
            'url': self.url,
            'duration': self.duration,
            'desc': self.desc,
            'tags': self.tags,
        }


class DailyStat(db.Model):
    """每日播放量统计表"""
    __tablename__ = 'daily_stats'

    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)  # 统计日期
    initial_views = db.Column(db.BigInteger, default=0)  # 当日初始播放量（0点或首次记录）
    current_views = db.Column(db.BigInteger, default=0)  # 当前播放量
    last_updated = db.Column(db.DateTime, default=datetime.now)  # 最后更新时间
    target = db.Column(db.BigInteger, default=0)  # 用户设定的目标播放量

    __table_args__ = (db.UniqueConstraint('video_id', 'date', name='uq_video_date'),)

    def to_dict(self):
        return {
            'id': self.id,
            'video_id': self.video_id,
            'date': self.date.strftime('%Y-%m-%d'),
            'initial_views': self.initial_views,
            'current_views': self.current_views,
            'growth': self.current_views - self.initial_views,
            'target': self.target,
            'achieved': self.current_views >= self.target if self.target > 0 else False,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
        }


# 创建数据库表
with app.app_context():
    db.create_all()


# ==================== B站API工具函数 ====================

def get_headers():
    """获取请求头"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }


def is_bv_id(input_str):
    """判断输入是否为BV号"""
    return bool(re.match(r'^BV[a-zA-Z0-9]+$', input_str))


def is_url(input_str):
    """判断输入是否为URL"""
    return input_str.startswith('http://') or input_str.startswith('https://')


def extract_bvid_from_url(url):
    """从B站视频URL中提取BV号"""
    match = re.search(r'/(?:video/)?(BV[a-zA-Z0-9]+)', url)
    if match:
        return match.group(1)
    return None


def extract_fav_id_from_url(url):
    """从B站收藏夹URL中提取收藏夹ID"""
    # 支持格式: https://www.bilibili.com/medialist/play/123456
    # 或: https://www.bilibili.com/medialist/detail/ml123456
    match = re.search(r'/medialist/(?:play|detail)/(\d+)', url)
    if match:
        return match.group(1)
    return None


def get_video_info_by_bvid(bvid):
    """通过BV号获取视频信息"""
    api_url = 'https://api.bilibili.com/x/web-interface/view'
    params = {'bvid': bvid}
    headers = get_headers()

    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data['code'] == 0:
            vdata = data['data']
            return {
                'aid': str(vdata['aid']),
                'bvid': vdata['bvid'],
                'title': vdata['title'],
                'author': vdata['owner']['name'],
                'author_id': str(vdata['owner']['mid']),
                'url': f"https://www.bilibili.com/video/{vdata['bvid']}",
                'duration': vdata['duration'],
                'desc': vdata['desc'],
                'tags': ','.join([tag['tag_name'] for tag in vdata.get('tags', [])]),
                'views': vdata['stat']['view'],
                'danmaku': vdata['stat']['danmaku'],
                'likes': vdata['stat']['like'],
                'coins': vdata['stat']['coin'],
                'favorites': vdata['stat']['favorite'],
                'shares': vdata['stat']['share'],
                'pubdate': vdata['pubdate'],
            }
        else:
            print(f"API错误: {data['message']}")
            return None
    except Exception as e:
        print(f"请求失败: {e}")
        return None


def get_fav_list(fav_id, page=1, page_size=20):
    """获取收藏夹中的视频列表"""
    api_url = 'https://api.bilibili.com/x/v3/fav/resource/list'
    params = {
        'media_id': fav_id,
        'pn': page,
        'ps': page_size,
        'platform': 'web'
    }
    headers = get_headers()

    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data['code'] == 0:
            result = data['data']
            videos = []
            for item in result.get('medias', []):
                # 从收藏夹条目中提取BV号
                bvid = item.get('bvid')
                if bvid:
                    videos.append({
                        'bvid': bvid,
                        'title': item.get('title', ''),
                        'author': item.get('upper', {}).get('name', ''),
                        'author_id': str(item.get('upper', {}).get('mid', '')),
                        'aid': str(item.get('id', '')),
                        'cover': item.get('cover', ''),
                        'url': f"https://www.bilibili.com/video/{bvid}",
                    })
            return {
                'total': result.get('total_count', 0),
                'videos': videos,
                'page': page,
                'page_size': page_size,
            }
        else:
            print(f"收藏夹API错误: {data['message']}")
            return None
    except Exception as e:
        print(f"请求收藏夹失败: {e}")
        return None


def get_all_fav_videos(fav_id):
    """获取收藏夹中所有视频（自动翻页）"""
    all_videos = []
    page = 1
    page_size = 50

    while True:
        result = get_fav_list(fav_id, page, page_size)
        if not result:
            break

        all_videos.extend(result['videos'])

        total = result['total']
        if page * page_size >= total:
            break
        page += 1
        time.sleep(0.5)  # 避免请求过快

    return all_videos


def get_current_views(bvid):
    """获取视频当前播放量"""
    info = get_video_info_by_bvid(bvid)
    if info:
        return info['views']
    return None


# ==================== 数据库操作函数 ====================

def get_or_create_video(bvid):
    """获取或创建视频记录"""
    video = Video.query.filter_by(bvid=bvid).first()
    if video:
        return video

    # 从API获取视频信息
    info = get_video_info_by_bvid(bvid)
    if not info:
        return None

    video = Video(
        aid=info['aid'],
        bvid=info['bvid'],
        title=info['title'],
        author=info['author'],
        author_id=info['author_id'],
        url=info['url'],
        duration=info['duration'],
        desc=info['desc'],
        tags=info['tags'],
    )
    db.session.add(video)
    db.session.commit()
    return video


def get_or_create_daily_stat(video_id, target_date=None):
    """获取或创建每日统计记录"""
    if target_date is None:
        target_date = date.today()

    stat = DailyStat.query.filter_by(video_id=video_id, date=target_date).first()
    if stat:
        return stat

    # 创建新的每日统计
    # 获取当前播放量作为初始播放量
    video = Video.query.get(video_id)
    if not video:
        return None

    views = get_current_views(video.bvid)
    if views is None:
        # 如果获取失败，使用0作为初始值
        views = 0

    stat = DailyStat(
        video_id=video_id,
        date=target_date,
        initial_views=views,
        current_views=views,
        last_updated=datetime.now(),
        target=0,
    )
    db.session.add(stat)
    db.session.commit()
    return stat


def update_current_views(video_id):
    """更新视频的当前播放量"""
    video = Video.query.get(video_id)
    if not video:
        return None

    views = get_current_views(video.bvid)
    if views is None:
        return None

    today = date.today()
    stat = DailyStat.query.filter_by(video_id=video_id, date=today).first()

    if stat:
        stat.current_views = views
        stat.last_updated = datetime.now()
        db.session.commit()
        return stat
    else:
        # 如果今日没有记录，创建一条
        return get_or_create_daily_stat(video_id, today)


def update_target(video_id, target_value):
    """更新视频的目标播放量"""
    today = date.today()
    stat = DailyStat.query.filter_by(video_id=video_id, date=today).first()
    if not stat:
        stat = get_or_create_daily_stat(video_id, today)
        if not stat:
            return None

    stat.target = target_value
    db.session.commit()
    return stat


def get_video_dashboard_data(bvid):
    """获取视频的仪表盘数据"""
    video = get_or_create_video(bvid)
    if not video:
        return None

    today = date.today()
    stat = get_or_create_daily_stat(video.id, today)
    if not stat:
        return None

    # 返回数据
    return {
        'video': video.to_dict(),
        'stat': stat.to_dict(),
        'growth': stat.current_views - stat.initial_views,
        'achieved': stat.current_views >= stat.target if stat.target > 0 else False,
    }


def get_fav_dashboard_data(fav_id):
    """获取收藏夹所有视频的仪表盘数据"""
    fav_videos = get_all_fav_videos(fav_id)
    if not fav_videos:
        return None

    result = []
    for item in fav_videos:
        bvid = item.get('bvid')
        if not bvid:
            continue

        # 获取或创建视频
        video = get_or_create_video(bvid)
        if not video:
            continue

        today = date.today()
        stat = get_or_create_daily_stat(video.id, today)
        if not stat:
            continue

        # 更新当前播放量（获取最新数据）
        views = get_current_views(bvid)
        if views is not None:
            stat.current_views = views
            stat.last_updated = datetime.now()
            db.session.commit()

        result.append({
            'video': video.to_dict(),
            'stat': stat.to_dict(),
            'growth': stat.current_views - stat.initial_views,
            'achieved': stat.current_views >= stat.target if stat.target > 0 else False,
        })

    return result


# ==================== Flask 路由 ====================

@app.route('/')
def index():
    """首页"""
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """仪表盘页面"""
    bvid = request.args.get('bvid', '').strip()
    fav_id = request.args.get('fav_id', '').strip()

    if not bvid and not fav_id:
        return render_template('index.html', error='请输入BV号或收藏夹号')

    data = None
    error = None
    is_fav = False

    if bvid:
        # 处理BV号
        if not is_bv_id(bvid):
            # 尝试从URL中提取BV号
            if is_url(bvid):
                extracted = extract_bvid_from_url(bvid)
                if extracted:
                    bvid = extracted
                else:
                    error = '无法从URL中提取BV号，请检查输入'
            else:
                error = '请输入有效的BV号（如：BV1GJ411x7fR）'

        if not error:
            data = get_video_dashboard_data(bvid)
            if not data:
                error = f'获取视频数据失败，请检查BV号是否正确：{bvid}'

    elif fav_id:
        # 处理收藏夹号
        if is_url(fav_id):
            extracted = extract_fav_id_from_url(fav_id)
            if extracted:
                fav_id = extracted
            else:
                error = '无法从URL中提取收藏夹ID，请检查输入'

        if not error:
            data = get_fav_dashboard_data(fav_id)
            if not data:
                error = f'获取收藏夹数据失败，请检查收藏夹ID是否正确：{fav_id}'
            else:
                is_fav = True

    if error:
        return render_template('index.html', error=error)

    if not data:
        return render_template('index.html', error='未获取到任何数据')

    if is_fav:
        return render_template('dashboard.html', data=data, fav_id=fav_id, is_fav=True)
    else:
        return render_template('dashboard.html', data=[data], bvid=bvid, is_fav=False)


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """刷新视频播放量 API"""
    video_id = request.json.get('video_id')
    if not video_id:
        return jsonify({'success': False, 'error': '缺少video_id参数'})

    stat = update_current_views(video_id)
    if not stat:
        return jsonify({'success': False, 'error': '更新失败'})

    video = Video.query.get(video_id)
    return jsonify({
        'success': True,
        'data': {
            'video_id': video_id,
            'current_views': stat.current_views,
            'initial_views': stat.initial_views,
            'growth': stat.current_views - stat.initial_views,
            'target': stat.target,
            'achieved': stat.current_views >= stat.target if stat.target > 0 else False,
            'last_updated': stat.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
            'bvid': video.bvid if video else '',
            'title': video.title if video else '',
        }
    })


@app.route('/api/update_target', methods=['POST'])
def api_update_target():
    """更新目标播放量 API"""
    video_id = request.json.get('video_id')
    target = request.json.get('target')

    if not video_id:
        return jsonify({'success': False, 'error': '缺少video_id参数'})

    try:
        target = int(target)
        if target < 0:
            return jsonify({'success': False, 'error': '目标值不能为负数'})
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '目标值必须是数字'})

    stat = update_target(video_id, target)
    if not stat:
        return jsonify({'success': False, 'error': '更新失败'})

    video = Video.query.get(video_id)
    return jsonify({
        'success': True,
        'data': {
            'video_id': video_id,
            'target': stat.target,
            'achieved': stat.current_views >= stat.target if stat.target > 0 else False,
            'bvid': video.bvid if video else '',
        }
    })


# ==================== 启动应用 ====================

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)