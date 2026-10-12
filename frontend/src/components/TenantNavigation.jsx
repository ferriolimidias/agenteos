import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { LayoutDashboard, Brain, BookOpenText, Users, Timer, MessageCircle, Tags, Megaphone, CalendarDays, Headphones, BarChart3, Building2, Instagram, ChevronDown, FileText, DollarSign, Settings } from "lucide-react";
import { shellStyles } from "../utils/uiStyles";
import { getTenantNavigationGroups, readCollapsedGroups, saveCollapsedGroups, visibleCollapsedGroups } from "../utils/tenantNavigation";

const icons = { dashboard: LayoutDashboard, brain: Brain, book: BookOpenText, users: Users, timer: Timer, message: MessageCircle, tags: Tags, campaign: Megaphone, calendar: CalendarDays, headphones: Headphones, chart: BarChart3, building: Building2, instagram: Instagram, file: FileText, costs: DollarSign, settings: Settings };

function preferenceStorage() {
  try { return window.localStorage; } catch { return null; }
}

export default function TenantNavigation({ role, preferenceKey, context, visitKey, onNavigate }) {
  const groups = getTenantNavigationGroups(role);
  const [preferences, setPreferences] = useState(() => ({
    collapsed: readCollapsedGroups(preferenceStorage(), preferenceKey, groups),
    manualCollapse: null,
    visitKey,
  }));
  // A new location, including back/forward to an earlier key, reveals its section.
  if (preferences.visitKey !== visitKey) {
    setPreferences({ ...preferences, visitKey, manualCollapse: null });
  }
  const collapsed = visibleCollapsedGroups(preferences.collapsed, context?.group.id, preferences.manualCollapse, visitKey);

  useEffect(() => {
    saveCollapsedGroups(preferenceStorage(), preferenceKey, preferences.collapsed, groups);
  }, [preferenceKey, preferences.collapsed, groups]);

  const toggleGroup = (groupId) => {
    const closing = !collapsed.includes(groupId);
    setPreferences((previous) => ({
      ...previous,
      collapsed: [...previous.collapsed.filter((id) => id !== groupId), ...(closing ? [groupId] : [])],
      manualCollapse: groupId === context?.group.id
        ? (closing ? { groupId, visitKey } : null)
        : previous.manualCollapse,
    }));
  };

  return (
    <nav className="fm-sidebar-nav fm-tenant-navigation" aria-label="Menu principal">
      {groups.map((group) => {
        const expanded = !collapsed.includes(group.id);
        const active = context?.group.id === group.id;
        return (
          <section key={group.id} className={`fm-navigation-group ${active ? "fm-navigation-group-active" : ""}`} data-navigation-group={group.id}>
            <h2 className="fm-navigation-heading">
              <button type="button" className="fm-navigation-toggle" aria-expanded={expanded} aria-controls={`tenant-group-${group.id}`} onClick={() => toggleGroup(group.id)}>
                <span>{group.label}</span>
                <ChevronDown size={15} aria-hidden="true" />
                {active && <span className="sr-only"> — seção atual</span>}
              </button>
            </h2>
            <ul id={`tenant-group-${group.id}`} hidden={!expanded} className="fm-navigation-items">
              {group.items.map((entry) => {
                const Icon = icons[entry.icon];
                const current = context?.item.id === entry.id;
                return (
                  <li key={entry.id}>
                    <Link to={entry.to} data-navigation-item={entry.id} aria-current={current ? "page" : undefined} className={`${shellStyles.navItem} ${current ? shellStyles.navActive : shellStyles.navInactive}`} onClick={onNavigate}>
                      <Icon aria-hidden="true" />
                      <span>{entry.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}
