import { NavLink, Outlet, useLocation } from "react-router-dom";
import { UserButton } from "@clerk/clerk-react";
import {
  LayoutDashboard,
  GitBranch,
  Shield,
} from "lucide-react";

const navItems = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/repositories", icon: GitBranch, label: "Repositories" },
];

export function Layout() {
  const location = useLocation();

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <NavLink to="/dashboard" className="sidebar-logo">
          <div className="sidebar-logo-icon">
            <Shield size={16} />
          </div>
          <span className="sidebar-logo-text">RepoSentry</span>
        </NavLink>

        <nav className="sidebar-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              location.pathname === item.to ||
              location.pathname.startsWith(item.to + "/");
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={`nav-item ${isActive ? "active" : ""}`}
              >
                <Icon size={18} />
                {item.label}
              </NavLink>
            );
          })}
        </nav>

        <div className="sidebar-spacer" />

        <div className="sidebar-user">
          <UserButton
            appearance={{
              elements: { avatarBox: { width: 32, height: 32 } },
            }}
          />
        </div>
      </aside>

      <div className="main-content">
        <Outlet />
      </div>
    </div>
  );
}
