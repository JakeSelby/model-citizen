import { Navigate, useLocation } from "react-router-dom";

import { HOME_PATH, legacyTarget } from "../navigation";

/** Sends an old route, or any unknown one, to its page in the regrouped nav, keeping its query and fragment. */
export function LegacyRedirect() {
  const { pathname, search, hash } = useLocation();
  return <Navigate replace to={{ pathname: legacyTarget(pathname) ?? HOME_PATH, search, hash }} />;
}
