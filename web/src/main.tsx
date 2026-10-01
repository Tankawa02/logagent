import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  RouterProvider,
  useNavigate,
} from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError } from './lib/api'
import { AuthRequired, SessionsPage } from './pages/SessionsPage'
import { validateWorkspaceSearch, Workspace, type WorkspaceSearch } from './pages/Workspace'
import './styles.css'

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
    <div className="p-10 text-center text-sm text-gray-500">
      页面不存在。<Link to="/" className="text-sky-700 hover:underline">返回会话列表</Link>
    </div>
  ),
})

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: SessionsPage })

const sessionRoute = createRoute({
  getParentRoute: () => rootRoute,
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

const shareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/s/$token',
  validateSearch: validateWorkspaceSearch,
  component: function ShareWorkspace() {
    const { token } = shareRoute.useParams()
    const search = shareRoute.useSearch()
    const navigate = useNavigate({ from: shareRoute.fullPath })
    return (
      <Workspace
        key={token}
        scope={{ kind: 'share', token }}
        search={search}
        setSearch={(next: WorkspaceSearch) => void navigate({ search: next, replace: true })}
      />
    )
  },
})

const router = createRouter({
  routeTree: rootRoute.addChildren([indexRoute, sessionRoute, shareRoute]),
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
