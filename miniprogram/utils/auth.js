// 微信登录 + token 持久化
const { request } = require('./request.js');

const STORAGE_KEY = 'ppt_token';
const PLACEHOLDER_HOST_RE = /your-domain\.example\.com/i;

function getApp_() { return getApp(); }

function persist(token, expiresAt, user) {
  try {
    wx.setStorageSync(STORAGE_KEY, { token: token, expiresAt: expiresAt, user: user });
  } catch (_e) {}
  const app = getApp_();
  app.globalData.token = token;
  app.globalData.expiresAt = expiresAt;
  app.globalData.user = user;
}

function clear() {
  try { wx.removeStorageSync(STORAGE_KEY); } catch (_e) {}
  const app = getApp_();
  app.globalData.token = null;
  app.globalData.expiresAt = null;
  app.globalData.user = null;
}

function restoreToken() {
  try {
    const cached = wx.getStorageSync(STORAGE_KEY);
    if (cached && cached.token) {
      const exp = cached.expiresAt ? new Date(cached.expiresAt).getTime() : 0;
      if (exp && exp > Date.now() + 30000) {
        const app = getApp_();
        app.globalData.token = cached.token;
        app.globalData.expiresAt = cached.expiresAt;
        app.globalData.user = cached.user;
        return true;
      }
    }
  } catch (_e) {}
  return false;
}

function isLoggedIn() {
  const app = getApp_();
  if (!app.globalData.token) return false;
  if (!app.globalData.expiresAt) return true;
  return new Date(app.globalData.expiresAt).getTime() > Date.now() + 30000;
}

/**
 * 标准微信登录流程：wx.login -> code -> 后端 /auth/wechat/login
 */
function loginWithWeChat(extra) {
  const app = getApp_();
  const baseUrl = (app.globalData.baseUrl || '').trim();

  if (!baseUrl || PLACEHOLDER_HOST_RE.test(baseUrl)) {
    return Promise.reject({
      code: 'config_baseurl',
      message: '后端地址未配置：请编辑 miniprogram/app.js 中的 baseUrl 为真实 HTTPS 服务器域名'
    });
  }
  if (!/^https?:\/\//i.test(baseUrl)) {
    return Promise.reject({
      code: 'config_baseurl',
      message: 'baseUrl 必须以 http(s):// 开头：' + baseUrl
    });
  }

  return new Promise(function (resolve, reject) {
    wx.login({
      success(res) {
        if (!res.code) {
          return reject({
            code: 'wx_login_no_code',
            message: 'wx.login 未返回 code（可能是 AppID 与开发者账号不匹配）'
          });
        }
        request({
          method: 'POST',
          path: '/api/v1/auth/wechat/login',
          auth: false,
          retries: 0,
          data: Object.assign({ code: res.code }, extra || {})
        }).then(function (data) {
          if (!data || !data.token) {
            return reject({ code: 'bad_response', message: '后端登录响应缺少 token 字段' });
          }
          persist(data.token, data.expires_at, data.user);
          resolve(data);
        }).catch(function (err) {
          // 把网络错误细节透出
          const msg = (err && err.message) || '后端登录失败';
          reject({
            code: (err && err.code) || 'login_failed',
            status: err && err.status,
            message: msg + (err && err.status ? '（HTTP ' + err.status + '）' : '')
          });
        });
      },
      fail(err) {
        reject({
          code: 'wx_login_fail',
          message: 'wx.login 调用失败：' + (err && err.errMsg ? err.errMsg : '未知错误')
        });
      }
    });
  });
}

function logout() {
  return request({ method: 'POST', path: '/api/v1/auth/logout' })
    .catch(function () { return null; })
    .then(function () { clear(); });
}

module.exports = {
  loginWithWeChat: loginWithWeChat,
  restoreToken: restoreToken,
  isLoggedIn: isLoggedIn,
  logout: logout,
  clear: clear
};
