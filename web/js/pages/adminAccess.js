import { el, clear } from "../util.js";
import { pageHeader } from "../components.js";
import { adminView } from "../adminView.js";
import * as users from "./adminUsers.js";
import * as classes from "./adminClasses.js";

let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }
export async function render(mount, ctx = {}) {
  dispose(); const view = adminView(); activeView = view;
  view.own(users.dispose); view.own(classes.dispose);
  const usersHost = el("section", { class: "card access-users" });
  const classesHost = el("section", { class: "card access-classes" });
  mount.append(pageHeader("班级与账号", "管理班主任账号、班级归属和各班级 Agent。新班级由班主任通过创建向导建立。"), usersHost, classesHost);
  const refreshUsers = () => { if (view.active) { clear(usersHost); return users.render(usersHost, { ...ctx, onChanged: refreshClasses }); } };
  const refreshClasses = () => { if (view.active) { clear(classesHost); return classes.render(classesHost, { ...ctx, onChanged: refreshUsers }); } };
  await Promise.all([refreshUsers(), refreshClasses()]);
}
