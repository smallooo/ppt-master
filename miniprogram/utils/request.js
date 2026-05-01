// 统一封装 wx.request，注入 baseUrl + bearer token，解析后端 ResponseEnvelope。
function getApp_() {
  // eslint-disable-next-line no-undef
  return getApp();
}

const RETRYABLE_STATUSES = [0, 502, 503, 504];

function _wait(ms) { return new Promise(r => setTimeout(r, ms)); }

function _baseUrl() {
  return getApp_().globalData.baseUrl.replace(/\/$/, '');
}

function _buildHeader(options) {
  const app = getApp_();
  const header = Object.assign(
    { 'Content-Type': 'application/json' },
    options.header || {}
  );
  if (options.auth !== false && app.globalData.token) {
    header.Authorization = 'Bearer ' + app.globalData.token;
  }
  if (options.admin && app.globalData.adminToken) {
    header['X-Admin-Token'] = app.globalData.adminToken;
  }
  return header;
}

function _handle401() {
  const app = getApp_();
  app.globalData.token = null;
  app.globalData.user = null;
  app.globalData.expiresAt = null;
  try { wx.removeStorageSync('ppt_token'); } catch (_e) {}
}

/**
 * @param {object} options
 *   method:  GET/POST/PATCH/DELETE
 *   path:    eg. '/api/v1/mini/projects'
 *   data:    JSON body / query
 *   header:  extra headers
 *   auth:    boolean (default true)
 *   admin:   boolean (default false)
 *   raw:     boolean — return full body without unwrapping
 *   retries: number — extra attempts on retryable failure (default 1)
 */
function request(options) {
  const retries = options.retries === undefined ? 1 : Math.max(0, options.retries | 0);
  const app = getApp_();

  function attempt(left) {
    return new Promise(function (resolve, reject) {
      if (!app.globalData.online) {
        return reject({ status: 0, code: 'offline', message: '网络不可用，请检查连接' });
      }
      wx.request({
        url: _baseUrl() + options.path,
        method: options.method || 'GET',
        data: options.data,
        header: _buildHeader(options),
        timeout: options.timeout || 30000,
        success(res) {
          const body = res.data || {};
          if (res.statusCode >= 200 && res.statusCode < 300) {
            if (options.raw) return resolve(body);
            return resolve(body.data !== undefined ? body.data : body);
          }
          const err = (body && body.error) || {};
          const msg = err.message || ('HTTP ' + res.statusCode);
          if (res.statusCode === 401) _handle401();
          if (RETRYABLE_STATUSES.indexOf(res.statusCode) >= 0 && left > 0) {
            return _wait(400).then(() => attempt(left - 1).then(resolve, reject));
          }
          reject({ status: res.statusCode, code: err.code || 'http_error', message: msg, details: err.details });
        },
        fail(err) {
          const msg = err.errMsg || 'network error';
          if (left > 0 && /timeout|fail|abort/i.test(msg)) {
            return _wait(400).then(() => attempt(left - 1).then(resolve, reject));
          }
          reject({ status: 0, code: 'network', message: '网络请求失败：' + msg });
        }
      });
    });
  }

  return attempt(retries);
}

function uploadFile(options) {
  const app = getApp_();
  if (!app.globalData.online) {
    return Promise.reject({ status: 0, code: 'offline', message: '网络不可用，请检查连接' });
  }
  const header = {};
  if (app.globalData.token) header.Authorization = 'Bearer ' + app.globalData.token;
  return new Promise(function (resolve, reject) {
    wx.uploadFile({
      url: _baseUrl() + options.path,
      filePath: options.filePath,
      name: 'file',
      formData: options.formData || {},
      header: header,
      timeout: options.timeout || 60000,
      success(res) {
        let body = {};
        try { body = JSON.parse(res.data); } catch (_e) { body = { raw: res.data }; }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(body.data !== undefined ? body.data : body);
        } else {
          if (res.statusCode === 401) _handle401();
          const err = (body && body.error) || {};
          reject({
            status: res.statusCode,
            code: err.code || 'http_error',
            message: err.message || ('HTTP ' + res.statusCode)
          });
        }
      },
      fail(err) {
        reject({ status: 0, code: 'network', message: err.errMsg || 'network error' });
      }
    });
  });
}

function downloadFile(path) {
  const app = getApp_();
  if (!app.globalData.online) {
    return Promise.reject({ status: 0, code: 'offline', message: '网络不可用，请检查连接' });
  }
  const header = {};
  if (app.globalData.token) header.Authorization = 'Bearer ' + app.globalData.token;
  return new Promise(function (resolve, reject) {
    wx.downloadFile({
      url: _baseUrl() + path,
      header: header,
      success(res) {
        if (res.statusCode === 200) resolve(res);
        else reject({ status: res.statusCode, message: '下载失败 (HTTP ' + res.statusCode + ')' });
      },
      fail(err) { reject({ status: 0, message: err.errMsg || '下载失败' }); }
    });
  });
}

module.exports = { request: request, uploadFile: uploadFile, downloadFile: downloadFile };
