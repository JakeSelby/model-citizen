import { Anchor } from "@mantine/core";
import { NavLink } from "react-router-dom";

import { FOOT_NAVIGATION, HOME_PATH, NAVIGATION_GROUPS, type NavItem } from "../navigation";
import { NavIcon } from "./NavIcon";

function NavItemLink({ item }: { item: NavItem }) {
  return (
    <Anchor aria-label={item.label} className="nav-link" component={NavLink} to={item.path} underline="never">
      <NavIcon name={item.key} />
      {item.label}
    </Anchor>
  );
}

/** The navbar's contents: the brand, the question groups, and Baseline pinned to the foot. */
export function StudioNav() {
  return (
    <>
      <Anchor className="brand" component={NavLink} to={HOME_PATH} underline="never">
        <span aria-hidden="true" className="brand-mark">MC</span>
        <span>Model Citizen <small>Studio</small></span>
      </Anchor>
      <div className="primary-navigation">
        {NAVIGATION_GROUPS.map((group) => {
          const links = group.items.map((item) => <NavItemLink item={item} key={item.path} />);
          if (!group.label) return <div className="nav-group" key="top">{links}</div>;
          const id = `nav-group-${group.label.toLowerCase()}`;
          return (
            <div aria-labelledby={id} className="nav-group" key={group.label} role="group">
              <div className="nav-group-label" id={id}>{group.label}</div>
              {links}
            </div>
          );
        })}
      </div>
      <div className="nav-foot">
        {FOOT_NAVIGATION.map((item) => <NavItemLink item={item} key={item.path} />)}
      </div>
    </>
  );
}
