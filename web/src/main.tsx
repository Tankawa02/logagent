import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createRootRoute, createRoute, createRouter, Link, Outlet, RouterProvider, useNavigate } from '@tanstack/react-router'
import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell } from './components/AppShell'
import { PageSkeleton, Spinner } from './components/ui'
import { api, ApiError } from './lib/api'
import { validateWorkspaceSearch, type WorkspaceSearch } from './lib/workspace-search'
import { AuthRequired } from './pages/AuthRequired'
import './styles.css'

// 每个页面一个 chunk：分享页只加载只读 Workspace，管理页不拖慢首屏
const MemoryPage = lazy(() => import('./pages/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const NewSession = lazy(() => import('./pages/NewSession').then((m) => ({ default: m.NewSession })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const SkillsPage = lazy(() => import('./pages/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const TraceDetail = lazy(() => import('./pages/TraceDetail').then((m) => ({ default: m.TraceDetail })))
const TraceList = lazy(() => import('./pages/TraceList').then((m) => ({ default: m.TraceList })))
const Workspace = lazy(() => import('./pages/Workspace').then((m) => ({ default: m.Workspace })))

function PageFallback() {
  return <PageSkeleton />
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // 4xx（令牌错误、会话不存在、链接过期）重试也没用
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
})

const rootRoute = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <div className="p-10 text-center text-sm text-zinc-500">
      页面不存在。
      <Link to="/" className="text-brand-700 hover:underline">
        返回首页
      </Link>
    </div>
  ),
})

function OwnerLayout() {
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  if (meta.isLoading) {
    return (
      <div className="flex h-dvh items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (meta.data && !meta.data.authenticated) return <AuthRequired />
  return (
    <AppShell>
      <Suspense fallback={<PageFallback />}>
        <Outlet />
      </Suspense>
    </AppShell>
  )
}

const ownerRoute = createRoute({ getParentRoute: () => rootRoute, id: 'owner', component: OwnerLayout })

const indexRoute = createRoute({
  getParentRoute: () => ownerRoute,
  path: '/',
  validateSearch: (search: Record<string, unknown>): { from?: string } => ({
    from: typeof search.from === 'string' && search.from ? search.from : undefined,
  }),
  component: function Index() {
    const { from } = indexRoute.useSearch()
    return <NewSession key={from ?? 'new'} from={from} />
  },
})

const sessionRoute = createRoute({
  getParentRoute: () => ownerRoute,
  path: '/sessions/$name',
  validateSearch: validateWorkspaceSearch,
  component: function OwnerWorkspace() {
    const { name } = sessionRoute.useParams()
    const search = sessionRoute.useSearch()
    const navigate = useNavigate({ from: sessionRoute.fullPath })
    return (
      <Workspace
        key={name}
        scope={{ kind: 'owner', name }}
        search={search}
        setSearch={(next: WorkspaceSearch) => void navigate({ search: next, replace: true })}
      />
    )
  },
  errorComponent: ({ error }) => (error instanceof ApiError && error.status === 401 ? <AuthRequired /> : <p>{String(error)}</p>),
})

const traceRoute = createRoute({
  getParentRoute: () => ownerRoute,
  path: '/trace',
  validateSearch: (search: Record<string, unknown>): { session?: string } => ({
    session: typeof search.session === 'string' && search.session ? search.session : undefined,
  }),
  component: function Trace() {
    const { session } = traceRoute.useSearch()
    return <TraceList key={session ?? 'all'} session={session} />
  },
})

const traceDetailRoute = createRoute({
  getParentRoute: () => ownerRoute,
  path: '/trace/$name/$turn',
  component: function TraceTurn() {
    const { name, turn } = traceDetailRoute.useParams()
    return <TraceDetail key={`${name}-${turn}`} name={name} turn={Number(turn)} />
  },
})

const skillsRoute = createRoute({ getParentRoute: () => ownerRoute, path: '/skills', component: SkillsPage })

const memoryRoute = createRoute({ getParentRoute: () => ownerRoute, path: '/memory', component: MemoryPage })

const settingsRoute = createRoute({ getParentRoute: () => ownerRoute, path: '/settings', component: SettingsPage })

const shareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/s/$token',
  validateSearch: validateWorkspaceSearch,
  component: function ShareWorkspace() {
    const { token } = shareRoute.useParams()
    const search = shareRoute.useSearch()
    const navigate = useNavigate({ from: shareRoute.fullPath })
    return (
      <div className="h-dvh bg-white dark:bg-zinc-950">
        <Suspense fallback={<PageFallback />}>
          <Workspace
            key={token}
            scope={{ kind: 'share', token }}
            search={search}
            setSearch={(next: WorkspaceSearch) => void navigate({ search: next, replace: true })}
          />
        </Suspense>
      </div>
    )
  },
})

const router = createRouter({
  routeTree: rootRoute.addChildren([
    ownerRoute.addChildren([indexRoute, sessionRoute, traceRoute, traceDetailRoute, skillsRoute, memoryRoute, settingsRoute]),
    shareRoute,
  ]),
  defaultPreload: false,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
