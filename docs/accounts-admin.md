# 账户、管理员与班级归属

## 初始管理员

应用首次启动时自动创建唯一管理员：

- 用户名：`admin`
- 初始密码：`32767`
- 角色：`admin`

首次登录后应立即调用 `POST /api/v1/auth/change-password` 修改密码。用户名经过 Unicode 规范化并按不区分大小写的方式保存，例如 `Teacher01` 与 `teacher01` 视为同一用户名。

`CLASSCLAW_API_TOKEN` 继续保留给 OpenClaw 插件和运维脚本使用，并被视作系统管理员凭证；普通网页用户使用 `/auth/login` 返回的会话令牌。

## 账户接口

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | 公开 | 用户名和密码登录 |
| GET | `/api/v1/auth/me` | 已登录 | 查看当前账号和班级 |
| POST | `/api/v1/auth/change-password` | 已登录 | 修改自己的密码 |
| POST | `/api/v1/auth/logout` | 已登录 | 注销当前会话 |
| GET | `/api/v1/admin/users` | 管理员 | 用户列表 |
| POST | `/api/v1/admin/users` | 管理员 | 新建班主任，默认密码为 `32767` |
| PATCH | `/api/v1/admin/users/{id}` | 管理员 | 修改显示名或停用账号 |
| POST | `/api/v1/admin/users/{id}/reset-password` | 管理员 | 重置为 `32767` 并注销旧会话 |
| POST | `/api/v1/admin/users/{id}/assign-class` | 管理员 | 把一个历史班级分配给班主任 |

系统不提供创建第二个管理员的接口，数据库也使用唯一部分索引保证最多一个 `admin`。普通用户角色固定为 `head_teacher`。

## 一账号一班级

班主任创建 onboarding 时，后端把用户 ID 写入引导会话；最终确认时，同一事务将它写入班级 `owner_user_id`。服务层和数据库唯一索引都会拒绝第二个班级。班主任只能列出自己的班级、访问自己的学生，并且只能创建或刷新自己班级的智能体和微信绑定。

迁移前已有班级没有归属，只有管理员可见。管理员可以通过 `assign-class` 将它分配给一个尚未拥有班级的班主任。

## 智能体和数据库调试

管理员可使用：

- `GET /api/v1/admin/agents`：查看班级、所有者、Agent、微信账号和错误状态。
- `POST /api/v1/admin/agents/{class_id}/wechat/start`：刷新 Agent 配置并重新生成微信二维码。
- `GET /api/v1/admin/database/overview`：查看数据库表及行数。
- `GET /api/v1/admin/database/tables/{table}?offset=0&limit=50`：分页查看只读表数据。

数据库调试接口不接受 SQL，不提供写入能力，并遮蔽 `users.password_hash` 和 `user_sessions.token_hash`。

## 升级与启动

```bash
cd /Users/wellon/classclaw
source .venv/bin/activate
alembic upgrade head
python run.py
```

启动后打开 <http://127.0.0.1:8000/app/test.html>。验收台提供后端功能地图、管理员和班主任登录、用户管理、班级归属、智能体/微信二维码、脱敏数据库浏览、自动只读巡检及通用 API 请求调试。
