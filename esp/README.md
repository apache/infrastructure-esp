# ESP Agent Basics

~~~python3
import asyncio
import esp.streams

async def processor_loop():
    # Grab an agent handle for the "github-stuff" role.
    agent = esp.streams.Agent("github-stuff")
    # Start reading entries assigned to 'github-stuff' role in a stream
    for entry in agent.read("some-stream"):
        # Process the entry somehow...
        some_data = process_entry(entry.data)
        
        # Let's make a new entry to another stream based on this entry 
        new_entry = entry.response()  # A new entry that tracks back to the original entry
        await agent.write("some-other-stream", new_entry)
        
        # Now mark processing of the original entry as completed, removing it from the PEL
        await entry.complete()

asyncio.run(processor_loop())
~~~
