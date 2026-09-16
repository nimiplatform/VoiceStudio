import { WorkspaceSidebar } from './workspace-sidebar';
import { CommandPalette } from '@/components/command-palette';
import { Outlet, useRouterState } from '@tanstack/react-router';
import { BackendGate } from '../backend-gate';

export function AppShell() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const settings = pathname.startsWith('/settings');
  const SettingsWorkspace = pathname === '/settings/openapi' ? 'div' : 'main';
  return (
    <div className="app-surface relative flex h-full flex-col bg-background text-foreground">
      <div className="flex min-h-0 flex-1">
        {settings ? (
          <>
            <CommandPalette />
            <SettingsWorkspace
              role={pathname === '/settings/openapi' ? 'main' : undefined}
              className="@container relative flex min-w-0 flex-1 flex-col overflow-hidden"
            >
              <div className="min-h-0 flex-1 overflow-hidden">
                <Outlet />
              </div>

            </SettingsWorkspace>
          </>
        ) : (
          <BackendGate>
            <CommandPalette />
            <WorkspaceSidebar />
            <main className="@container relative flex min-w-0 flex-1 flex-col overflow-hidden">
              <div className="min-h-0 flex-1 overflow-hidden">
                <Outlet />
              </div>

            </main>
          </BackendGate>
        )}
      </div>
    </div>
  );
}
