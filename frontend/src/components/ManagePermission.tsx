/** Link that asks Google for the optional "manage your YouTube account" permission. */
export const MANAGE_LOGIN_URL = '/api/auth/login?manage=true';

export function ManagePermissionNote({ action }: { action: string }) {
  return (
    <div role="note" className="rounded-sm border border-amber-400 bg-amber-50 p-2 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
      <p>To {action}, DownTube needs permission to manage your YouTube account. DownTube only uses it when you ask for an action like this.</p>
      <a className="btn mt-2" href={MANAGE_LOGIN_URL}>Grant permission with Google</a>
    </div>
  );
}
