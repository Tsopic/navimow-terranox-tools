Java.perform(function () {
  var terms = [
    "mowersetting",
    "workplan",
    "schedule",
    "plan",
    "netapi",
    "retrofit",
    "okhttp",
    "request",
    "encrypt",
    "decrypt"
  ];
  var prefixes = [
    "com.segway.mower",
    "cn.ninebot",
    "okhttp",
    "retrofit"
  ];
  var matches = [];

  Java.enumerateLoadedClasses({
    onMatch: function (name) {
      var lower = name.toLowerCase();
      var wantedPrefix = prefixes.some(function (prefix) {
        return name.indexOf(prefix) === 0;
      });
      var wantedTerm = terms.some(function (term) {
        return lower.indexOf(term) !== -1;
      });
      if (wantedPrefix && wantedTerm) {
        matches.push(name);
      }
    },
    onComplete: function () {
      matches.sort().forEach(function (name) {
        console.log("CLASS " + name);
      });

      [
        "com.segway.mower.device.device.setting.MowerSettingManager",
        "com.segway.mower.device.device.setting.WorkPlanFragment2",
        "com.segway.mower.datacenter.netapi.setting.bean.MowerDailyPlanBeanV2",
        "com.segway.mower.datacenter.netapi.setting.bean.PlanPeriodBeanV2",
        "com.segway.mower.datacenter.netapi.setting.bean.MowerSettingBean"
      ].forEach(function (name) {
        try {
          var klass = Java.use(name).class;
          console.log("METHODS " + name);
          klass.getDeclaredMethods().forEach(function (method) {
            console.log("  " + method.toString());
          });
        } catch (e) {
          console.log("MISS " + name + " " + e);
        }
      });
    }
  });
});
