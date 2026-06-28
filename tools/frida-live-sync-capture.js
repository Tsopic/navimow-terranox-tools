var CAPTURE_VALUES = false;

var READ_PATHS = {
  "/vehicle/vehicle/index2": true,
  "/vehicle/vehicle/get-device-info": true,
  "/vehicle/vehicle/get-location": true,
  "/vehicle/vehicle/set-list": true,
  "/vehicle/trail/get-path-info-time": true,
  "/vehicle/trail/get-path-info-data-compress": true,
  "/vehicle/vehicle/auth-list": true,
  "/mowerbot/vehicle/vehicle/state": true,
  "/vehicle/vehicle/get-vehicle-weather": true,
  "/vehicle/vehicle/get-today-plan": true,
  "/vehicle/firmware/get-new-firmware": true,
  "/vehicle/vehicle/get-component-maintenance": true,
  "/map/index/map-list": true,
  "/map/index/map-detail-compress": true,
  "/mowerbot/vehicle/common/get-iot-file": true
};

var emittedRequests = {};

function pathFromUrl(url) {
  try {
    var match = String(url).match(/^https?:\/\/[^/]+([^?#]*)/i);
    return match ? match[1] : String(url);
  } catch (e) {
    return String(url);
  }
}

function valueShape(value, depth) {
  if (depth > 5) return "max-depth";
  if (value === null) return "null";
  if (Array.isArray(value)) {
    return {
      type: "array",
      length: value.length,
      items: value.length ? valueShape(value[0], depth + 1) : "empty"
    };
  }
  if (typeof value === "object") {
    var result = {};
    Object.keys(value).sort().forEach(function (key) {
      result[key] = valueShape(value[key], depth + 1);
    });
    return result;
  }
  return typeof value;
}

function shapeKey(shape) {
  try {
    return JSON.stringify(shape);
  } catch (e) {
    return "shape-error";
  }
}

function captureHeaders(headers) {
  var result = {
    names: [],
    values: {}
  };
  try {
    var iterator = headers.names().iterator();
    while (iterator.hasNext()) {
      var name = String(iterator.next());
      result.names.push(name);
      if (CAPTURE_VALUES) {
        result.values[name] = String(headers.get(name));
      }
    }
  } catch (e) {
    result.error = String(e);
  }
  return result;
}

function captureBody(request) {
  var result = {};
  try {
    var body = request.body();
    if (body === null) return result;
    result.contentType = compact(body.contentType());
    try {
      result.contentLength = Number(body.contentLength());
    } catch (e1) {
      result.contentLengthError = String(e1);
    }
    var Buffer = Java.use("okio.Buffer");
    var buffer = Buffer.$new();
    body.writeTo(buffer);
    var raw = String(buffer.readUtf8());
    if (CAPTURE_VALUES) {
      result.text = raw;
      try {
        result.json = JSON.parse(raw);
      } catch (e2) {
        result.jsonParseError = String(e2);
      }
    } else {
      try {
        result.jsonShape = valueShape(JSON.parse(raw), 0);
      } catch (e2) {
        result.bodyKind = raw.length ? "non-json" : "empty";
      }
    }
  } catch (e) {
    result.error = String(e);
  }
  return result;
}

function compact(value) {
  if (value === null || value === undefined) return value;
  var text = String(value);
  if (text.length > 160) return text.slice(0, 160) + "...";
  return text;
}

function emitRecord(request, hookName) {
  try {
    var url = String(request.url());
    var path = pathFromUrl(url);
    if (!READ_PATHS[path]) return;
    var body = captureBody(request);
    var dedupeKey = [
      String(request.method()),
      path,
      body.contentType || "",
      body.contentLength || "",
      shapeKey(body.jsonShape || body.bodyKind || {})
    ].join("|");
    if (!CAPTURE_VALUES && emittedRequests[dedupeKey]) return;
    emittedRequests[dedupeKey] = true;
    var record = {
      kind: "navimow-live-sync-request",
      hook: hookName,
      method: String(request.method()),
      urlHost: String(request.url().host()),
      path: path,
      headers: captureHeaders(request.headers()),
      body: body
    };
    console.log("NAVIMOW_LIVE_SYNC " + JSON.stringify(record));
  } catch (e) {
    console.log("NAVIMOW_LIVE_SYNC " + JSON.stringify({
      kind: "navimow-live-sync-error",
      error: String(e)
    }));
  }
}

Java.perform(function () {
  try {
    var OkHttpClient = Java.use("okhttp3.OkHttpClient");
    OkHttpClient.newCall.overloads.forEach(function (overload) {
      overload.implementation = function (request) {
        emitRecord(request, "OkHttpClient.newCall");
        return overload.apply(this, arguments);
      };
    });
    try {
      var RealInterceptorChain = Java.use("okhttp3.internal.http.RealInterceptorChain");
      RealInterceptorChain.proceed.overloads.forEach(function (overload) {
        overload.implementation = function (request) {
          emitRecord(request, "RealInterceptorChain.proceed");
          return overload.apply(this, arguments);
        };
      });
    } catch (e2) {
      console.log("NAVIMOW_LIVE_SYNC " + JSON.stringify({
        kind: "navimow-live-sync-secondary-hook-error",
        error: String(e2)
      }));
    }
    console.log("NAVIMOW_LIVE_SYNC " + JSON.stringify({
      kind: "navimow-live-sync-hook-ready",
      captureValues: CAPTURE_VALUES
    }));
  } catch (e) {
    console.log("NAVIMOW_LIVE_SYNC " + JSON.stringify({
      kind: "navimow-live-sync-hook-error",
      error: String(e)
    }));
  }
});
