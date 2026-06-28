function compact(value) {
  if (value === null || value === undefined) return String(value);
  var text = String(value);
  text = text.replace(/[A-Z0-9]{14,}/g, "<id>");
  text = text.replace(/(authorization|token|cookie|password|secret|sn|serial|vehicle_sn|vehiclesn)(["'=:\\s]+)[^,"'\\s}]+/ig, "$1$2<redacted>");
  if (text.length > 500) {
    text = text.slice(0, 500) + "...<truncated>";
  }
  return text;
}

Java.perform(function () {
  function hook(className, methodName, overloadHandler) {
    try {
      var clazz = Java.use(className);
      clazz[methodName].overloads.forEach(function (overload) {
        overload.implementation = overloadHandler(clazz, overload);
      });
      console.log("HOOK " + className + "." + methodName);
    } catch (e) {
      console.log("NOHOOK " + className + "." + methodName + " " + e);
    }
  }

  hook("okhttp3.OkHttpClient", "newCall", function (_clazz, overload) {
    return function (request) {
      try {
        console.log("[okhttp.newCall] " + compact(request.method()) + " " + compact(request.url()));
      } catch (e) {
        console.log("[okhttp.newCall] " + e);
      }
      return overload.apply(this, arguments);
    };
  });

  hook("okhttp3.Request$Builder", "url", function (_clazz, overload) {
    return function () {
      try {
        if (arguments.length > 0) {
          console.log("[request.url] " + compact(arguments[0]));
        }
      } catch (e) {
        console.log("[request.url] " + e);
      }
      return overload.apply(this, arguments);
    };
  });

  hook("java.net.URL", "openConnection", function (_clazz, overload) {
    return function () {
      try {
        console.log("[url.openConnection] " + compact(this.toString()));
      } catch (e) {
        console.log("[url.openConnection] " + e);
      }
      return overload.apply(this, arguments);
    };
  });

  [
    "com.segway.mower.device.device.setting.MowerSettingManager",
    "com.segway.mower.device.device.setting.WorkPlanFragment2"
  ].forEach(function (className) {
    try {
      var clazz = Java.use(className);
      clazz.class.getDeclaredMethods().forEach(function (method) {
        var name = method.getName();
        if (/plan|setting|save|query|mow|cycle/i.test(String(name)) && clazz[name]) {
          clazz[name].overloads.forEach(function (overload) {
            overload.implementation = function () {
              var args = [];
              for (var i = 0; i < arguments.length; i++) {
                args.push(compact(arguments[i]));
              }
              console.log("[call] " + className + "." + name + "(" + args.join(", ") + ")");
              return overload.apply(this, arguments);
            };
          });
          console.log("HOOK " + className + "." + name);
        }
      });
    } catch (e) {
      console.log("NOHOOK " + className + " methods " + e);
    }
  });
});
