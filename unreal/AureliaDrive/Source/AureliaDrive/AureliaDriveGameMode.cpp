#include "AureliaDriveGameMode.h"

#include "AureliaVehiclePawn.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/SkyLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/SkyAtmosphere.h"
#include "Engine/SkyLight.h"
#include "GameFramework/PlayerController.h"

AAureliaDriveGameMode::AAureliaDriveGameMode()
{
	DefaultPawnClass = AAureliaVehiclePawn::StaticClass();
}

void AAureliaDriveGameMode::BeginPlay()
{
	Super::BeginPlay();

	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	FActorSpawnParameters Spawn;
	Spawn.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

	ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(FVector(0.f, 0.f, 800.f), FRotator(-6.5f, 195.f, 0.f), Spawn);
	if (Sun)
	{
		if (UDirectionalLightComponent* Light = Cast<UDirectionalLightComponent>(Sun->GetLightComponent()))
		{
			Light->SetIntensity(12.f);
			Light->SetLightColor(FLinearColor(1.f, 0.72f, 0.48f));
			Light->SetAtmosphereSunLight(true);
			Light->SetCastShadows(true);
			Light->bUseRayTracedDistanceFieldShadows = true;
		}
	}

	ASkyLight* SkyLight = World->SpawnActor<ASkyLight>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);
	if (SkyLight)
	{
		if (USkyLightComponent* Sky = SkyLight->GetLightComponent())
		{
			Sky->SetIntensity(1.15f);
			Sky->SetRealTimeCapture(true);
		}
	}

	World->SpawnActor<ASkyAtmosphere>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);

	AExponentialHeightFog* Fog = World->SpawnActor<AExponentialHeightFog>(FVector(0.f, 0.f, 200.f), FRotator::ZeroRotator, Spawn);
	if (Fog)
	{
		if (UExponentialHeightFogComponent* FogComp = Fog->GetComponent())
		{
			FogComp->SetFogDensity(0.018f);
			FogComp->SetFogHeightFalloff(0.12f);
			FogComp->SetFogInscatteringColor(FLinearColor(0.55f, 0.28f, 0.22f));
		}
	}

	if (APlayerController* PC = World->GetFirstPlayerController())
	{
		PC->bShowMouseCursor = false;
	}
}
