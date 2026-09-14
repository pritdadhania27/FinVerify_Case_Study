import React from 'react'
import ReactDOM from 'react-dom/client'
import { createHashRouter, RouterProvider } from 'react-router-dom'
import App from './App'
import Dashboard from './pages/Dashboard'
import DocumentsPage from './pages/Documents'
import DocumentViewer from './pages/DocumentViewer'
import AskPage from './pages/Ask'
import ArmsPage from './pages/Arms'
import VerificationPage from './pages/Verification'
import ResearchPage from './pages/Research'
import QuestionPage from './pages/Question'
import NotFound, { RouteError } from './pages/NotFound'
import './index.css'

// Hash routing: this is served as static files with no server rewrite rules, and
// browser routing would 404 on a refresh of any deep link.
//
// `errorElement` and the `*` catch-all are load-bearing, not decoration. Without
// them an unmatched hash rendered the app shell with an EMPTY content area - no
// message, nav intact - which reads as a page that failed to load rather than
// one that does not exist; and any render throw replaced the whole document with
// React Router's default stack trace. Both are attached to the layout route so
// the failure renders INSIDE the shell, leaving the nav usable.
const router = createHashRouter([
  {
    path: '/',
    element: <App />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'documents', element: <DocumentsPage /> },
      { path: 'documents/:id', element: <DocumentViewer /> },
      { path: 'ask', element: <AskPage /> },
      { path: 'arms', element: <ArmsPage /> },
      { path: 'verification', element: <VerificationPage /> },
      { path: 'questions/:qid', element: <QuestionPage /> },
      { path: 'research', element: <ResearchPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
