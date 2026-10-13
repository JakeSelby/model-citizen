import { Navigate, useLocation } from "react-router-dom";

import { legacyLocation } from "../navigation";

/** Sends an old route, or any unknown one, to its page in the regrouped nav, keeping its query and fragment. */
export function LegacyRedirect() {
  return <Navigate replace to={legacyLocation(useLocation())} />;
}
