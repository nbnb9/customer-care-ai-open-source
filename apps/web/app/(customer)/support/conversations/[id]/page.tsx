"use client";

import { useParams } from "next/navigation";
import ConversationPanel from "@/components/conversation/ConversationPanel";

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  return <ConversationPanel conversationId={params.id} />;
}
