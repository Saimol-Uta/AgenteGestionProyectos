import ReactMarkdown from "react-markdown";

export function SafeMarkdown({ source, className = "safe-markdown" }: { source: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown
        skipHtml
        components={{
          a: ({ href, children }) => <a href={href || undefined} target={href ? "_blank" : undefined} rel={href ? "noopener noreferrer" : undefined}>{children}</a>,
          img: () => null,
        }}
      >
        {source.replaceAll("\\n", "\n")}
      </ReactMarkdown>
    </div>
  );
}
